"""Turn transcribed rows into validated events.

Everything interpretive happens here, deterministically, so that it can be
tested without a network or a model. Every branch that cannot proceed raises;
none of them substitutes a default.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from .config import (
    HOURS_WINDOWS,
    MAX_ROW_COUNT_DROP,
    SANE_WINDOW_DAYS_AFTER,
    SANE_WINDOW_DAYS_BEFORE,
    VENUES,
)
from .errors import ValidationError

_MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11,
    "december": 12,
}

# "June 2", "June 2nd", "Jun. 2" -- no year, by design.
_DATE_RE = re.compile(r"^([A-Za-z]+)\.?\s+(\d{1,2})(?:st|nd|rd|th)?$")


def normalize(raw: str) -> str:
    """Collapse whitespace, including the non-breaking spaces Drupal emits."""
    return re.sub(r"[\s\u00a0]+", " ", raw).strip()


@dataclass(frozen=True)
class Event:
    """One validated table row, ready to render."""

    event_date: date
    venue: str          # "Oracle" / "Chase" -- the source token
    venue_display: str  # "Oracle Park" / "Chase Center"
    hours_text: str     # the source string, verbatim, for the description
    start_hour: int
    end_hour: int

    @property
    def uid_local(self) -> str:
        return f"{self.event_date.strftime('%Y%m%d')}-{self.venue.lower()}"

    @property
    def start(self) -> datetime:
        return datetime.combine(self.event_date, datetime.min.time()).replace(hour=self.start_hour)

    @property
    def end(self) -> datetime:
        return datetime.combine(self.event_date, datetime.min.time()).replace(hour=self.end_hour)


# --- individual field validators -------------------------------------------


def parse_month_day(raw: str, row_index: int) -> tuple[int, int]:
    """Return ``(month_index, day)`` for a year-less date cell."""
    cleaned = normalize(raw)
    match = _DATE_RE.match(cleaned)
    if not match:
        raise ValidationError(
            f"Row {row_index}: date {raw!r} does not look like a year-less "
            "'Month Day' value. Not guessing at it."
        )

    month_name, day_text = match.group(1).lower(), match.group(2)

    month = _MONTHS.get(month_name)
    if month is None:
        # Accept the three-letter abbreviations SFMTA occasionally uses.
        for name, index in _MONTHS.items():
            if name.startswith(month_name) and len(month_name) >= 3:
                month = index
                break
    if month is None:
        raise ValidationError(f"Row {row_index}: {match.group(1)!r} is not a month name (from {raw!r}).")

    day = int(day_text)
    if not 1 <= day <= 31:
        raise ValidationError(f"Row {row_index}: day {day} is out of range (from {raw!r}).")

    return month, day


def validate_venue(raw: str, row_index: int) -> str:
    """Return the canonical venue token, or raise."""
    cleaned = normalize(raw)
    for token in VENUES:
        if cleaned.lower() == token.lower():
            return token
    raise ValidationError(
        f"Row {row_index}: venue {raw!r} is not one of {sorted(VENUES)}.\n"
        "A third venue means SFMTA changed the scope of this notice; add it "
        "deliberately rather than letting it through."
    )


def validate_hours(raw: str, row_index: int) -> tuple[int, int]:
    """Return ``(start_hour, end_hour)`` for a whitelisted hours string."""
    cleaned = normalize(raw).strip().lower()
    window = HOURS_WINDOWS.get(cleaned)
    if window is None:
        raise ValidationError(
            f"Row {row_index}: rate hours {raw!r} (normalised to {cleaned!r}) are "
            f"not in the known whitelist {sorted(HOURS_WINDOWS)}.\n"
            "SFMTA has introduced a new enforcement window. Add it to "
            "HOURS_WINDOWS in sfmta_ics/config.py after checking the page."
        )
    return window


# --- year inference ---------------------------------------------------------


def infer_years(month_days: list[tuple[int, int]], effective: date) -> list[date]:
    """Attach years to year-less ``(month, day)`` pairs.

    Anchors on the effective date's year and rolls forward exactly once, at the
    December -> January boundary. More than one rollover means the page is no
    longer a single sub-twelve-month schedule, and the inference is void.
    """
    if not month_days:
        raise ValidationError("Year inference received zero rows.")

    year = effective.year
    rollovers = 0
    previous_month: int | None = None
    dates: list[date] = []

    for index, (month, day) in enumerate(month_days):
        if previous_month is not None and month < previous_month:
            year += 1
            rollovers += 1
            if rollovers > 1:
                raise ValidationError(
                    f"Row {index}: the month index decreased for a second time "
                    f"({previous_month} -> {month}), implying the schedule spans "
                    "more than twelve months.\n"
                    "A published SFMTA schedule never does. The table order or "
                    "the page structure has changed; the year inference is not "
                    "trustworthy and no calendar will be written."
                )

        try:
            resolved = date(year, month, day)
        except ValueError as exc:
            raise ValidationError(
                f"Row {index}: {year}-{month:02d}-{day:02d} is not a real date ({exc})."
            ) from exc

        dates.append(resolved)
        previous_month = month

    earliest = effective - timedelta(days=SANE_WINDOW_DAYS_BEFORE)
    latest = effective + timedelta(days=SANE_WINDOW_DAYS_AFTER)
    for index, resolved in enumerate(dates):
        if not earliest <= resolved <= latest:
            raise ValidationError(
                f"Row {index}: inferred date {resolved.isoformat()} falls outside "
                f"the sane window {earliest.isoformat()}..{latest.isoformat()} "
                f"around the effective date {effective.isoformat()}.\n"
                "The year inference has broken; no calendar will be written."
            )

    return dates


# --- quarantine -------------------------------------------------------------


def partition_rows(rows: list[dict[str, str]]) -> tuple[list[dict[str, str]], list[tuple[int, dict[str, str]]]]:
    """Split rows into (complete, quarantined-with-original-index).

    A row goes to quarantine when any cell is blank after normalisation --
    SFMTA does occasionally publish one. A row whose cells are all present but
    *invalid* (unknown venue, unknown hours) is NOT quarantined; those still
    fail the run in ``validate_rows``, because they need a deliberate whitelist
    decision rather than a shrug.
    """
    complete: list[dict[str, str]] = []
    quarantined: list[tuple[int, dict[str, str]]] = []
    for index, row in enumerate(rows):
        if all(normalize(row[key]) for key in ("date", "venue", "hours")):
            complete.append(row)
        else:
            quarantined.append((index, row))
    return complete, quarantined


# --- top level --------------------------------------------------------------


def validate_rows(rows: list[dict[str, str]], effective: date) -> list[Event]:
    """Validate every row and resolve years. Raises on the first problem."""
    if not rows:
        raise ValidationError(
            "Zero rows to validate. The source table is never empty; refusing to "
            "build an empty calendar."
        )

    month_days = [parse_month_day(row["date"], i) for i, row in enumerate(rows)]
    dates = infer_years(month_days, effective)

    events: list[Event] = []
    for index, (row, event_date) in enumerate(zip(rows, dates)):
        venue = validate_venue(row["venue"], index)
        start_hour, end_hour = validate_hours(row["hours"], index)
        events.append(
            Event(
                event_date=event_date,
                venue=venue,
                venue_display=VENUES[venue],
                hours_text=normalize(row["hours"]),
                start_hour=start_hour,
                end_hour=end_hour,
            )
        )

    seen: dict[str, int] = {}
    for index, event in enumerate(events):
        key = event.uid_local
        if key in seen:
            raise ValidationError(
                f"Rows {seen[key]} and {index} both resolve to UID {key!r} "
                f"({event.event_date.isoformat()}, {event.venue}).\n"
                "Two rows for the same date and venue would collapse into one "
                "calendar entry and silently lose data."
            )
        seen[key] = index

    return events


def check_row_count(current: int, previous: int | None) -> None:
    """Fail if the row count collapsed versus the last successful run."""
    if previous is None or previous == 0:
        return

    floor = previous * (1 - MAX_ROW_COUNT_DROP)
    if current < floor:
        drop = (previous - current) / previous
        raise ValidationError(
            f"Row count fell from {previous} to {current} "
            f"({drop:.0%} drop, limit {MAX_ROW_COUNT_DROP:.0%}).\n"
            "That is a broken extraction far more often than a genuinely short "
            "schedule. The existing calendar has been left untouched.\n"
            "Check the notice page. If the schedule genuinely did shrink that "
            f'much, lower "row_count" in state/events.json to {current} and '
            "re-run -- the state file is only updated on a successful run, so "
            "re-running alone will keep hitting this."
        )
