"""SEQUENCE bookkeeping: the thing that makes clients apply revisions."""

from __future__ import annotations

import json
from datetime import date, datetime

import pytest

from sfmta_ics.errors import FatalError
from sfmta_ics.state import (
    State,
    assign_last_modified,
    assign_sequences,
    build_state,
    load_state,
    save_state,
)
from sfmta_ics.validate import Event

RUN_TIME = datetime(2026, 6, 3, 13, 10, 0)


def make_event(day: int, venue: str = "Oracle", start: int = 18, end: int = 22) -> Event:
    return Event(
        event_date=date(2026, 6, day),
        venue=venue,
        venue_display="Oracle Park" if venue == "Oracle" else "Chase Center",
        hours_text="6 P.M. to 10 P.M.",
        start_hour=start,
        end_hour=end,
    )


def state_with(event: Event, sequence: int) -> State:
    return State(
        events={
            event.uid_local: {
                "start": event.start.isoformat(),
                "end": event.end.isoformat(),
                "sequence": sequence,
                "last_modified": "20260101T000000Z",
            }
        },
        row_count=1,
    )


def test_a_new_event_starts_at_zero():
    event = make_event(8)
    assert assign_sequences([event], State.empty()) == {"20260608-oracle": 0}


def test_an_unchanged_event_keeps_its_sequence():
    event = make_event(8)
    previous = state_with(event, sequence=3)
    assert assign_sequences([event], previous) == {"20260608-oracle": 3}


def test_changed_hours_bump_the_sequence():
    published = make_event(8, start=18, end=22)
    revised = make_event(8, start=12, end=18)
    previous = state_with(published, sequence=3)
    assert assign_sequences([revised], previous) == {"20260608-oracle": 4}


def test_changed_end_time_alone_bumps_the_sequence():
    published = make_event(8, start=18, end=22)
    revised = make_event(8, start=18, end=21)
    assert assign_sequences([revised], state_with(published, 0)) == {"20260608-oracle": 1}


def test_same_date_different_venue_is_a_separate_uid_and_sequence():
    oracle, chase = make_event(10, "Oracle"), make_event(10, "Chase")
    previous = state_with(oracle, sequence=5)
    assert assign_sequences([oracle, chase], previous) == {
        "20260610-oracle": 5,
        "20260610-chase": 0,
    }


def test_last_modified_only_moves_when_the_event_moved():
    unchanged = make_event(8)
    previous = state_with(unchanged, sequence=2)
    stamps = assign_last_modified([unchanged], {"20260608-oracle": 2}, previous, RUN_TIME)
    assert stamps["20260608-oracle"] == "20260101T000000Z"

    revised = make_event(8, start=12, end=18)
    stamps = assign_last_modified([revised], {"20260608-oracle": 3}, previous, RUN_TIME)
    assert stamps["20260608-oracle"] == "20260603T131000Z"


def test_vanished_events_are_pruned_from_the_state():
    kept, dropped = make_event(8), make_event(9)
    previous = State(
        events={
            kept.uid_local: {"start": kept.start.isoformat(), "end": kept.end.isoformat(), "sequence": 1},
            dropped.uid_local: {"start": dropped.start.isoformat(), "end": dropped.end.isoformat(), "sequence": 4},
        },
        row_count=2,
    )
    sequences = assign_sequences([kept], previous)
    stamps = assign_last_modified([kept], sequences, previous, RUN_TIME)
    new_state = build_state([kept], sequences, stamps, RUN_TIME)

    assert set(new_state.events) == {"20260608-oracle"}
    assert new_state.row_count == 1


def test_state_round_trips_through_disk(tmp_path):
    event = make_event(8)
    sequences = assign_sequences([event], State.empty())
    stamps = assign_last_modified([event], sequences, State.empty(), RUN_TIME)
    path = tmp_path / "events.json"

    save_state(build_state([event], sequences, stamps, RUN_TIME), path)
    reloaded = load_state(path)

    assert reloaded.row_count == 1
    assert reloaded.events["20260608-oracle"]["sequence"] == 0
    assert reloaded.last_success_utc == "2026-06-03T13:10:00Z"


def test_a_missing_state_file_is_a_first_run_not_an_error(tmp_path):
    assert load_state(tmp_path / "nope.json") == State.empty()


def test_a_corrupt_state_file_is_fatal_rather_than_silently_reset(tmp_path):
    path = tmp_path / "events.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(FatalError, match="not valid JSON"):
        load_state(path)


def test_a_state_file_with_the_wrong_shape_is_fatal(tmp_path):
    path = tmp_path / "events.json"
    path.write_text(json.dumps({"events": {"x": {"start": "a"}}}), encoding="utf-8")
    with pytest.raises(FatalError, match="must have"):
        load_state(path)
