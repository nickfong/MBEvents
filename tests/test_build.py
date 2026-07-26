"""The generated ICS must survive a real iCalendar parser."""

from __future__ import annotations

from datetime import date, datetime

import pytest
from icalendar import Calendar

from sfmta_ics.build import build_calendar, fold
from sfmta_ics.main import validate_ics
from sfmta_ics.state import State, assign_last_modified, assign_sequences
from sfmta_ics.validate import validate_rows

HOST = "nickfong.github.io"
RUN_TIME = datetime(2026, 6, 3, 13, 10, 0)


@pytest.fixture
def calendar_text(parsed_rows, effective_date):
    events = validate_rows(parsed_rows, effective_date)
    sequences = assign_sequences(events, State.empty())
    stamps = assign_last_modified(events, sequences, State.empty(), RUN_TIME)
    return events, build_calendar(events, sequences, RUN_TIME, stamps, HOST)


def test_calendar_round_trips_through_icalendar(calendar_text):
    events, text = calendar_text
    calendar = Calendar.from_ical(text)

    assert calendar.get("VERSION") == "2.0"
    assert calendar.get("CALSCALE") == "GREGORIAN"
    assert calendar.get("METHOD") == "PUBLISH"
    assert "mbevents" in str(calendar.get("PRODID"))
    assert len(list(calendar.walk("VEVENT"))) == len(events) == 34


def test_calendar_name_and_staleness_marker(calendar_text):
    _events, text = calendar_text
    calendar = Calendar.from_ical(text)

    assert str(calendar.get("X-WR-CALNAME")) == "SFMTA Special Event Parking"
    caldesc = str(calendar.get("X-WR-CALDESC"))
    assert "2026-06-03 13:10 UTC" in caldesc
    assert "sfmta.com" in caldesc


def test_refresh_hints_are_present(calendar_text):
    _events, text = calendar_text
    assert "REFRESH-INTERVAL;VALUE=DURATION:PT12H" in text
    assert "X-PUBLISHED-TTL:PT12H" in text


def test_there_is_exactly_one_real_vtimezone(calendar_text):
    _events, text = calendar_text
    calendar = Calendar.from_ical(text)
    timezones = list(calendar.walk("VTIMEZONE"))

    assert len(timezones) == 1
    assert str(timezones[0].get("TZID")) == "America/Los_Angeles"
    assert len(list(timezones[0].walk("DAYLIGHT"))) == 1
    assert len(list(timezones[0].walk("STANDARD"))) == 1


def test_times_are_tzid_qualified_never_floating_or_utc(calendar_text):
    _events, text = calendar_text
    for component in Calendar.from_ical(text).walk("VEVENT"):
        for field in ("DTSTART", "DTEND"):
            assert component.get(field).params["TZID"] == "America/Los_Angeles"

    # No un-parameterised (floating) or Z-suffixed (UTC) start inside a VEVENT.
    # VTIMEZONE's own DTSTARTs are bare by design, so only look past it.
    vevents = text.split("END:VTIMEZONE", 1)[1]
    assert "DTSTART:" not in vevents
    assert "DTEND:" not in vevents
    assert "T220000Z" not in vevents


def test_uids_are_stable_date_venue_hostname(calendar_text):
    _events, text = calendar_text
    uids = [str(c.get("UID")) for c in Calendar.from_ical(text).walk("VEVENT")]

    assert uids[0] == f"20260602-chase@{HOST}"
    assert f"20260710-oracle@{HOST}" in uids
    assert f"20260710-chase@{HOST}" in uids
    assert len(set(uids)) == len(uids)


def test_same_day_rows_are_not_merged(calendar_text):
    _events, text = calendar_text
    july_10 = [
        c for c in Calendar.from_ical(text).walk("VEVENT")
        if c.get("DTSTART").dt.date() == date(2026, 7, 10)
    ]
    assert len(july_10) == 2
    assert {str(c.get("LOCATION")) for c in july_10} == {"Oracle Park", "Chase Center"}


def test_noon_maps_to_twelve_hundred(calendar_text):
    _events, text = calendar_text
    (event,) = [
        c for c in Calendar.from_ical(text).walk("VEVENT")
        if str(c.get("UID")).startswith("20260610-oracle")
    ]
    assert (event.get("DTSTART").dt.hour, event.get("DTEND").dt.hour) == (12, 18)


def test_three_pm_window_maps_correctly(calendar_text):
    _events, text = calendar_text
    (event,) = [
        c for c in Calendar.from_ical(text).walk("VEVENT")
        if str(c.get("UID")).startswith("20260801-oracle")
    ]
    assert (event.get("DTSTART").dt.hour, event.get("DTEND").dt.hour) == (15, 22)


def test_summary_and_description_describe_meter_hours_not_game_times(calendar_text):
    _events, text = calendar_text
    component = next(iter(Calendar.from_ical(text).walk("VEVENT")))

    summary = str(component.get("SUMMARY"))
    description = str(component.get("DESCRIPTION"))

    assert summary == "Special event meter rates: Chase Center"
    assert "meter enforcement window, not the event start time" in description
    assert "subject to change" in description
    assert "https://www.sfmta.com/notices/" in description


def test_sequence_is_emitted_per_event(parsed_rows, effective_date):
    events = validate_rows(parsed_rows, effective_date)
    sequences = {event.uid_local: 0 for event in events}
    sequences[events[0].uid_local] = 7
    stamps = assign_last_modified(events, sequences, State.empty(), RUN_TIME)

    text = build_calendar(events, sequences, RUN_TIME, stamps, HOST)
    component = next(iter(Calendar.from_ical(text).walk("VEVENT")))
    assert int(component.get("SEQUENCE")) == 7


def test_building_with_zero_events_raises():
    with pytest.raises(ValueError, match="zero events"):
        build_calendar([], {}, RUN_TIME, {}, HOST)


def test_lines_are_crlf_terminated_and_folded_to_75_octets(calendar_text):
    _events, text = calendar_text
    assert text.endswith("\r\n")
    for line in text.split("\r\n"):
        assert len(line.encode("utf-8")) <= 75, line


def test_fold_leaves_short_lines_alone():
    assert fold("UID:short@example.com") == "UID:short@example.com"


def test_fold_never_splits_a_multibyte_character():
    line = "DESCRIPTION:" + "é" * 200
    folded = fold(line)
    assert "".join(segment.lstrip(" ") if i else segment for i, segment in enumerate(folded.split("\r\n"))) == line


def test_validate_ics_rejects_a_uid_mismatch(calendar_text):
    events, text = calendar_text
    expected = [f"{event.uid_local}@{HOST}" for event in events]

    validate_ics(text, expected)  # the happy path

    from sfmta_ics.errors import FatalError

    with pytest.raises(FatalError, match="unexpected or reordered UIDs"):
        validate_ics(text, list(reversed(expected)))

    with pytest.raises(FatalError, match="expected"):
        validate_ics(text, expected[:-1])
