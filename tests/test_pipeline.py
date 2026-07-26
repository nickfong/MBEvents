"""End-to-end over the fixture, with the network and the model stubbed out.

Also pins the two properties that matter most operationally: a failure writes
nothing, and a success always refreshes the heartbeat.
"""

from __future__ import annotations

import json
from datetime import date

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


def test_past_events_survive_rolloff_but_future_removals_do_not(
    wired, parsed_rows, monkeypatch
):
    """The whole point of the archive, end to end."""
    ics_path, state_path, _heartbeat = wired

    # First run on the full fixture, with "today" before every event.
    monkeypatch.setattr(main_mod, "today_pacific", lambda: date(2026, 6, 1))
    assert main_mod.main() == 0
    assert json.loads(state_path.read_text(encoding="utf-8"))["row_count"] == 34

    # SFMTA republishes: most of June has rolled off the page, and one future
    # date (19 October, Chase) has been cancelled. The drop is kept under the
    # 40% guard -- see the dedicated test below for what happens past it.
    rolled_off = {"June 2", "June 8", "June 9", "June 10", "June 12", "June 13",
                  "June 14", "June 15", "June 23", "June 24", "June 25", "June 26"}
    shrunk = [
        row for row in parsed_rows
        if row["date"] not in rolled_off and row["date"] != "October 19"
    ]
    monkeypatch.setattr(main_mod, "parse_rows", lambda _text: shrunk)
    monkeypatch.setattr(main_mod, "today_pacific", lambda: date(2026, 7, 1))

    assert main_mod.main() == 0

    text = ics_path.read_text(encoding="utf-8")
    saved = json.loads(state_path.read_text(encoding="utf-8"))

    # June is past and gone from the page: archived, still published.
    assert "20260602-chase" in text
    assert "20260626-oracle" in text
    assert "20260602-chase" in saved["events"]

    # October is still in the future and was removed: treated as a cancellation.
    assert "20261019-chase" not in text
    assert "20261019-chase" not in saved["events"]

    # row_count follows the scrape, not the published total.
    assert saved["row_count"] == len(shrunk) == 21
    assert text.count("BEGIN:VEVENT") == 33, "21 scraped + 12 archived June dates"
    assert len(saved["events"]) == 33


def test_a_rolloff_bigger_than_the_guard_still_stops_the_run(wired, parsed_rows, monkeypatch, capsys):
    """The archive does not weaken the row-count guard, by design.

    A republication that drops more than 40% of the rows halts even when the
    loss is legitimate rolloff. That is the conservative choice: the guard
    cannot tell rolloff from a broken extraction, and a wrong calendar is worse
    than a stopped job. Recovery is the documented manual row_count edit.
    """
    _ics, _state, _heartbeat = wired
    monkeypatch.setattr(main_mod, "today_pacific", lambda: date(2026, 6, 1))
    assert main_mod.main() == 0

    monkeypatch.setattr(main_mod, "today_pacific", lambda: date(2026, 7, 1))
    monkeypatch.setattr(
        main_mod, "parse_rows", lambda _text: [r for r in parsed_rows if not r["date"].startswith("June")]
    )

    assert main_mod.main() == 1
    assert "Row count fell from 34 to 20" in capsys.readouterr().err


def test_archived_events_keep_their_sequence_when_the_page_moves_on(
    wired, parsed_rows, monkeypatch
):
    ics_path, state_path, _heartbeat = wired
    monkeypatch.setattr(main_mod, "today_pacific", lambda: date(2026, 6, 1))

    # Publish, then revise June 2's hours so its sequence bumps to 1.
    assert main_mod.main() == 0
    revised = [dict(row) for row in parsed_rows]
    revised[0]["hours"] = "Noon to 6 P.M."
    monkeypatch.setattr(main_mod, "parse_rows", lambda _text: revised)
    assert main_mod.main() == 0
    assert json.loads(state_path.read_text(encoding="utf-8"))["events"]["20260602-chase"]["sequence"] == 1

    # Now June rolls off the page entirely. The archived copy must keep
    # sequence 1 and the revised times, not reset or drift.
    monkeypatch.setattr(main_mod, "today_pacific", lambda: date(2026, 7, 1))
    monkeypatch.setattr(
        main_mod, "parse_rows", lambda _text: [r for r in revised if r["date"] != "June 2"]
    )
    assert main_mod.main() == 0

    record = json.loads(state_path.read_text(encoding="utf-8"))["events"]["20260602-chase"]
    assert record["sequence"] == 1
    assert record["start"] == "2026-06-02T12:00:00"
    assert "DTSTART;TZID=America/Los_Angeles:20260602T120000" in ics_path.read_text(encoding="utf-8")


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
