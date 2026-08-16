"""Year inference, the time whitelist, venues, and the row-count floor."""

from __future__ import annotations

from datetime import date

import pytest

from sfmta_ics.errors import ValidationError
from sfmta_ics.validate import (
    check_row_count,
    infer_years,
    parse_month_day,
    partition_rows,
    validate_hours,
    validate_rows,
    validate_venue,
)

EFFECTIVE = date(2026, 6, 2)


# --- year inference ---------------------------------------------------------


def test_year_is_taken_from_the_effective_date_when_months_ascend():
    dates = infer_years([(6, 2), (7, 10), (10, 19)], EFFECTIVE)
    assert dates == [date(2026, 6, 2), date(2026, 7, 10), date(2026, 10, 19)]


def test_december_to_january_rolls_the_year_forward_once():
    effective = date(2026, 11, 15)
    dates = infer_years([(11, 20), (12, 26), (1, 3), (2, 14)], effective)
    assert dates == [
        date(2026, 11, 20),
        date(2026, 12, 26),
        date(2027, 1, 3),
        date(2027, 2, 14),
    ]


def test_a_second_rollover_is_rejected():
    # Dec -> Jan -> Dec -> Jan implies a schedule spanning over a year.
    with pytest.raises(ValidationError, match="second time"):
        infer_years([(12, 1), (1, 5), (12, 2), (1, 6)], date(2026, 11, 1))


def test_dates_past_the_end_of_the_window_are_rejected():
    # Rolls over once, then runs on to December of the following year, which is
    # beyond effective + 400 days.
    with pytest.raises(ValidationError, match="sane window"):
        infer_years([(2, 1), (1, 1), (12, 31)], date(2026, 1, 1))


def test_dates_before_the_window_are_rejected():
    # An early-in-the-year row under a late-in-the-year effective date: the
    # anchor year is wrong and the result lands months in the past.
    with pytest.raises(ValidationError, match="sane window"):
        infer_years([(1, 2)], date(2026, 6, 2))


def test_impossible_calendar_date_is_rejected():
    with pytest.raises(ValidationError, match="not a real date"):
        infer_years([(2, 30)], date(2026, 2, 1))


def test_zero_rows_into_year_inference_raises():
    with pytest.raises(ValidationError, match="zero rows"):
        infer_years([], EFFECTIVE)


# --- date cells -------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("June 2", (6, 2)), ("October 19", (10, 19)), ("August  1", (8, 1)), ("Sept 3", (9, 3))],
)
def test_month_day_parsing(raw, expected):
    assert parse_month_day(raw, 0) == expected


@pytest.mark.parametrize("raw", ["", "2 June", "June", "Junuary 4", "6/2", "TBD"])
def test_unparseable_dates_raise(raw):
    with pytest.raises(ValidationError):
        parse_month_day(raw, 0)


# --- venues -----------------------------------------------------------------


@pytest.mark.parametrize(("raw", "expected"), [("Oracle", "Oracle"), ("chase", "Chase"), (" Oracle ", "Oracle")])
def test_known_venues_are_accepted(raw, expected):
    assert validate_venue(raw, 0) == expected


@pytest.mark.parametrize("raw", ["Oracle Park", "Kezar", "", "Both"])
def test_unknown_venues_raise(raw):
    with pytest.raises(ValidationError, match="not one of"):
        validate_venue(raw, 0)


# --- time whitelist ---------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Noon to 6 P.M.", (12, 18)),
        ("3 P.M. to 10 P.M.", (15, 22)),
        ("6 P.M. to 10 P.M.", (18, 22)),
        ("  6 p.m.  to 10 p.m. ", (18, 22)),
        ("Noon to 6 P.M.", (12, 18)),
    ],
)
def test_whitelisted_hours(raw, expected):
    assert validate_hours(raw, 0) == expected


@pytest.mark.parametrize("raw", ["5 P.M. to 11 P.M.", "Noon to 5 P.M.", "All day", "", "6 PM - 10 PM"])
def test_unknown_hours_raise_and_name_the_offender(raw):
    with pytest.raises(ValidationError) as excinfo:
        validate_hours(raw, 7)
    assert repr(raw) in str(excinfo.value)
    assert "config.py" in str(excinfo.value)


# --- whole-table validation -------------------------------------------------


def test_fixture_rows_validate_cleanly(parsed_rows):
    events = validate_rows(parsed_rows, EFFECTIVE)
    assert len(events) == 34
    assert events[0].event_date == date(2026, 6, 2)
    assert events[0].venue_display == "Chase Center"
    assert events[-1].event_date == date(2026, 10, 19)


def test_same_date_two_venues_produce_two_distinct_uids(parsed_rows):
    events = validate_rows(parsed_rows, EFFECTIVE)
    july_10 = [e.uid_local for e in events if e.event_date == date(2026, 7, 10)]
    assert july_10 == ["20260710-oracle", "20260710-chase"]


def test_two_rows_with_the_same_date_and_venue_are_rejected():
    rows = [
        {"date": "June 2", "venue": "Oracle", "hours": "6 P.M. to 10 P.M."},
        {"date": "June 2", "venue": "Oracle", "hours": "Noon to 6 P.M."},
    ]
    with pytest.raises(ValidationError, match="same UID|resolve to UID"):
        validate_rows(rows, EFFECTIVE)


def test_zero_rows_raises_rather_than_building_an_empty_calendar():
    with pytest.raises(ValidationError, match="Zero rows"):
        validate_rows([], EFFECTIVE)


# --- quarantine partition ---------------------------------------------------


def test_a_blank_hours_cell_is_quarantined_not_fatal():
    rows = [
        {"date": "June 2", "venue": "Chase", "hours": "6 P.M. to 10 P.M."},
        {"date": "September 19", "venue": "Chase", "hours": ""},
    ]
    complete, quarantined = partition_rows(rows)
    assert complete == rows[:1]
    assert quarantined == [(1, rows[1])]


def test_whitespace_only_cells_count_as_blank():
    rows = [{"date": "June 2", "venue": "   ", "hours": "6 P.M. to 10 P.M."}]
    complete, quarantined = partition_rows(rows)
    assert complete == [] and len(quarantined) == 1


def test_complete_rows_are_never_quarantined(parsed_rows):
    complete, quarantined = partition_rows(parsed_rows)
    assert complete == parsed_rows and quarantined == []


def test_a_present_but_invalid_value_is_still_fatal_not_quarantined():
    # Quarantine is for blanks only. Garbage that is present must still fail,
    # because it needs a whitelist decision, not a shrug.
    rows = [{"date": "June 2", "venue": "Chase", "hours": "5 P.M. to 11 P.M."}]
    complete, quarantined = partition_rows(rows)
    assert quarantined == []
    with pytest.raises(ValidationError, match="whitelist"):
        validate_rows(complete, EFFECTIVE)


# --- row-count floor --------------------------------------------------------


def test_first_run_has_no_previous_count_to_compare():
    check_row_count(34, None)
    check_row_count(34, 0)


def test_a_modest_drop_is_allowed():
    check_row_count(21, 34)  # ~38%, just inside the 40% limit


def test_a_collapse_is_rejected():
    with pytest.raises(ValidationError, match="Row count fell"):
        check_row_count(3, 74)


def test_growth_is_always_fine():
    check_row_count(120, 34)
