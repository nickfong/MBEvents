"""The archive: past events survive rolloff, future removals are cancellations.

SFMTA only publishes a rolling window. Rebuilding purely from the page would
delete finished dates out of subscribers' calendars as the window advances.
"""

from __future__ import annotations

import json
from datetime import date, datetime

import pytest

from sfmta_ics.errors import FatalError
from sfmta_ics.state import (
    State,
    archived_events,
    assign_last_modified,
    assign_sequences,
    build_state,
)
from sfmta_ics.validate import Event

RUN_TIME = datetime(2026, 8, 1, 13, 10, 0)
TODAY = date(2026, 8, 1)


def make_event(day: int, venue: str = "Oracle", start: int = 18, end: int = 22, month: int = 7) -> Event:
    return Event(
        event_date=date(2026, month, day),
        venue=venue,
        venue_display="Oracle Park" if venue == "Oracle" else "Chase Center",
        hours_text="6 P.M. to 10 P.M." if start == 18 else "Noon to 6 P.M.",
        start_hour=start,
        end_hour=end,
    )


def record_for(event: Event, sequence: int = 0, last_modified: str = "20260701T000000Z") -> dict:
    return {
        "start": event.start.isoformat(),
        "end": event.end.isoformat(),
        "venue": event.venue,
        "hours": event.hours_text,
        "sequence": sequence,
        "last_modified": last_modified,
    }


def state_of(*pairs) -> State:
    return State(events={e.uid_local: record_for(e, s) for e, s in pairs}, row_count=len(pairs))


# --- the core rule ----------------------------------------------------------


def test_a_past_event_that_left_the_page_is_archived():
    gone = make_event(10)  # 10 July, before TODAY
    previous = state_of((gone, 0))

    archived = archived_events(previous, scraped_uids=set(), today=TODAY)

    assert [e.uid_local for e in archived] == ["20260710-oracle"]
    assert archived[0].venue_display == "Oracle Park"
    assert archived[0].hours_text == "6 P.M. to 10 P.M."
    assert (archived[0].start_hour, archived[0].end_hour) == (18, 22)


def test_a_future_event_that_left_the_page_is_a_cancellation_and_is_dropped():
    cancelled = make_event(15, month=9)  # 15 September, after TODAY
    previous = state_of((cancelled, 0))

    assert archived_events(previous, scraped_uids=set(), today=TODAY) == []


def test_an_event_still_on_the_page_is_not_archived():
    current = make_event(10)
    previous = state_of((current, 0))

    # It is in the scrape, so it comes from the page, not the archive.
    assert archived_events(previous, {"20260710-oracle"}, TODAY) == []


def test_todays_event_is_not_yet_past_so_it_still_mirrors_the_page():
    today_event = make_event(1, month=8)  # exactly TODAY
    previous = state_of((today_event, 0))

    assert archived_events(previous, scraped_uids=set(), today=TODAY) == []


def test_the_boundary_is_strictly_before_today():
    yesterday = make_event(31, month=7)
    previous = state_of((yesterday, 0))

    assert len(archived_events(previous, set(), TODAY)) == 1


# --- freezing ---------------------------------------------------------------


def test_an_archived_event_keeps_its_sequence_and_last_modified():
    gone = make_event(10)
    previous = state_of((gone, 4))
    previous.events[gone.uid_local]["last_modified"] = "20260705T090000Z"

    archived = archived_events(previous, set(), TODAY)
    sequences = assign_sequences(archived, previous)
    stamps = assign_last_modified(archived, sequences, previous, RUN_TIME)

    assert sequences == {"20260710-oracle": 4}
    assert stamps == {"20260710-oracle": "20260705T090000Z"}


def test_archived_events_do_not_drift_across_repeated_runs():
    gone = make_event(10)
    state = state_of((gone, 2))

    for _ in range(3):
        archived = archived_events(state, set(), TODAY)
        sequences = assign_sequences(archived, state)
        stamps = assign_last_modified(archived, sequences, state, RUN_TIME)
        state = build_state(archived, sequences, stamps, RUN_TIME, scraped_count=0)

    record = state.events["20260710-oracle"]
    assert record["sequence"] == 2
    assert record["last_modified"] == "20260701T000000Z"


# --- state assembly ---------------------------------------------------------


def test_row_count_tracks_the_scrape_not_the_archive():
    archived, scraped = make_event(10), make_event(20, month=9)
    events = [archived, scraped]
    sequences = {e.uid_local: 0 for e in events}
    stamps = {e.uid_local: "20260801T131000Z" for e in events}

    state = build_state(events, sequences, stamps, RUN_TIME, scraped_count=1)

    assert len(state.events) == 2
    assert state.row_count == 1, "a growing archive must not mask a collapsed scrape"


def test_state_stores_enough_to_rebuild_the_event(tmp_path):
    event = make_event(10, start=12, end=18)
    sequences, stamps = {event.uid_local: 0}, {event.uid_local: "20260801T131000Z"}

    state = build_state([event], sequences, stamps, RUN_TIME, scraped_count=1)
    record = state.events["20260710-oracle"]

    assert record["venue"] == "Oracle"
    assert record["hours"] == "Noon to 6 P.M."
    assert json.loads(json.dumps(record)) == record, "must be JSON-serialisable"


# --- migration from the pre-archive state file ------------------------------


def test_a_legacy_record_without_venue_or_hours_is_still_recoverable():
    # v1 records carried only start/end/sequence. The venue is in the UID and
    # the hours are implied by the window, so no data is actually lost.
    previous = State(
        events={
            "20260710-chase": {
                "start": "2026-07-10T18:00:00",
                "end": "2026-07-10T22:00:00",
                "sequence": 1,
            }
        },
        row_count=1,
    )

    (event,) = archived_events(previous, set(), TODAY)

    assert event.venue == "Chase"
    assert event.venue_display == "Chase Center"
    assert event.hours_text == "6 P.M. to 10 P.M."


def test_a_legacy_record_with_an_unknown_window_refuses_to_guess():
    previous = State(
        events={
            "20260710-oracle": {
                "start": "2026-07-10T09:00:00",
                "end": "2026-07-10T11:00:00",
                "sequence": 0,
            }
        },
        row_count=1,
    )

    with pytest.raises(FatalError, match="not a known one"):
        archived_events(previous, set(), TODAY)


def test_a_record_with_an_unknown_venue_is_rejected():
    previous = State(
        events={
            "20260710-kezar": {
                "start": "2026-07-10T18:00:00",
                "end": "2026-07-10T22:00:00",
                "sequence": 0,
            }
        },
        row_count=1,
    )

    with pytest.raises(FatalError, match="not one of"):
        archived_events(previous, set(), TODAY)


def test_a_record_with_an_unparseable_start_is_rejected():
    previous = State(
        events={"20260710-oracle": {"start": "not-a-date", "end": "also-not", "sequence": 0}},
        row_count=1,
    )

    with pytest.raises(FatalError, match="unparseable"):
        archived_events(previous, set(), TODAY)
