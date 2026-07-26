"""End-to-end over the fixture, with the network and the model stubbed out.

Also pins the two properties that matter most operationally: a failure writes
nothing, and a success always refreshes the heartbeat.
"""

from __future__ import annotations

import json

import pytest

from sfmta_ics import main as main_mod
from sfmta_ics import state as state_mod
from sfmta_ics.errors import AuthError, FetchError


@pytest.fixture
def wired(monkeypatch, tmp_path, notice_html, parsed_rows):
    """Point the pipeline at temp paths with the fetch and parse steps stubbed."""
    ics_path = tmp_path / "docs" / "sfmta-events.ics"
    state_path = tmp_path / "state" / "events.json"
    heartbeat_path = tmp_path / "state" / "last-run.txt"

    monkeypatch.setattr(main_mod, "ICS_PATH", ics_path)
    monkeypatch.setattr(main_mod, "STATE_PATH", state_path)
    monkeypatch.setattr(main_mod, "HEARTBEAT_PATH", heartbeat_path)
    monkeypatch.setattr(main_mod, "fetch_page", lambda: notice_html)
    monkeypatch.setattr(main_mod, "parse_rows", lambda _text: parsed_rows)

    return ics_path, state_path, heartbeat_path


def test_a_full_successful_run(wired, capsys):
    ics_path, state_path, heartbeat_path = wired

    assert main_mod.main() == 0

    text = ics_path.read_text(encoding="utf-8")
    assert text.startswith("BEGIN:VCALENDAR")
    assert text.rstrip().endswith("END:VCALENDAR")
    assert text.count("BEGIN:VEVENT") == 34

    saved = json.loads(state_path.read_text(encoding="utf-8"))
    assert saved["row_count"] == 34
    assert len(saved["events"]) == 34
    assert all(record["sequence"] == 0 for record in saved["events"].values())

    assert heartbeat_path.read_text(encoding="utf-8").strip().endswith("Z")
    assert "34 events" in capsys.readouterr().out


def test_a_second_run_with_revised_hours_bumps_only_that_sequence(wired, parsed_rows, monkeypatch):
    ics_path, state_path, _heartbeat = wired

    assert main_mod.main() == 0
    first = json.loads(state_path.read_text(encoding="utf-8"))
    assert first["events"]["20260608-oracle"]["sequence"] == 0

    revised = [dict(row) for row in parsed_rows]
    revised[1]["hours"] = "Noon to 6 P.M."  # June 8, Oracle
    monkeypatch.setattr(main_mod, "parse_rows", lambda _text: revised)

    assert main_mod.main() == 0
    second = json.loads(state_path.read_text(encoding="utf-8"))

    assert second["events"]["20260608-oracle"]["sequence"] == 1
    unchanged = [uid for uid in second["events"] if uid != "20260608-oracle"]
    assert all(second["events"][uid]["sequence"] == 0 for uid in unchanged)
    assert "DTSTART;TZID=America/Los_Angeles:20260608T120000" in ics_path.read_text(encoding="utf-8")


def test_a_removed_event_is_pruned_from_state_and_calendar(wired, parsed_rows, monkeypatch):
    ics_path, state_path, _heartbeat = wired
    assert main_mod.main() == 0

    monkeypatch.setattr(main_mod, "parse_rows", lambda _text: parsed_rows[:-1])
    assert main_mod.main() == 0

    saved = json.loads(state_path.read_text(encoding="utf-8"))
    assert "20261019-chase" not in saved["events"]
    assert "20261019-chase" not in ics_path.read_text(encoding="utf-8")


def test_a_fetch_failure_leaves_the_previous_calendar_untouched(wired, monkeypatch, capsys):
    ics_path, state_path, heartbeat_path = wired

    assert main_mod.main() == 0
    good_ics = ics_path.read_text(encoding="utf-8")
    good_state = state_path.read_text(encoding="utf-8")
    good_heartbeat = heartbeat_path.read_text(encoding="utf-8")

    def boom():
        raise FetchError("SFMTA returned HTTP 503")

    monkeypatch.setattr(main_mod, "fetch_page", boom)

    assert main_mod.main() == 1
    assert ics_path.read_text(encoding="utf-8") == good_ics
    assert state_path.read_text(encoding="utf-8") == good_state
    assert heartbeat_path.read_text(encoding="utf-8") == good_heartbeat

    stderr = capsys.readouterr().err
    assert "FATAL" in stderr and "503" in stderr
    assert "previously published calendar and state file are unchanged" in stderr


def test_a_bad_row_writes_nothing_at_all(wired, parsed_rows, monkeypatch, capsys):
    ics_path, state_path, heartbeat_path = wired

    broken = [dict(row) for row in parsed_rows]
    broken[5]["hours"] = "5 P.M. to 11 P.M."
    monkeypatch.setattr(main_mod, "parse_rows", lambda _text: broken)

    assert main_mod.main() == 1
    assert not ics_path.exists()
    assert not state_path.exists()
    assert not heartbeat_path.exists()
    assert "5 P.M. to 11 P.M." in capsys.readouterr().err


def test_an_auth_failure_prints_the_regeneration_instructions(wired, monkeypatch, capsys):
    def boom(_text):
        raise AuthError("OAuth token rejected by the CLI")

    monkeypatch.setattr(main_mod, "parse_rows", boom)

    assert main_mod.main() == 1

    stderr = capsys.readouterr().err
    assert "authentication failed" in stderr
    assert "claude setup-token" in stderr
    assert "CLAUDE_CODE_OAUTH_TOKEN" in stderr
    assert "Settings -> Secrets" in stderr


def test_a_collapsed_row_count_stops_the_run(wired, parsed_rows, monkeypatch, capsys):
    ics_path, _state_path, _heartbeat = wired
    assert main_mod.main() == 0
    good_ics = ics_path.read_text(encoding="utf-8")

    monkeypatch.setattr(main_mod, "parse_rows", lambda _text: parsed_rows[:5])

    assert main_mod.main() == 1
    assert ics_path.read_text(encoding="utf-8") == good_ics
    assert "Row count fell from 34 to 5" in capsys.readouterr().err
