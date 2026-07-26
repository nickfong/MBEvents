"""Pipeline entry point.

Order matters: nothing touches the disk until the calendar has been built in
memory *and* re-parsed by a real iCalendar implementation. Any failure before
that point leaves ``docs/sfmta-events.ics`` and ``state/events.json`` exactly as
the last successful run left them, so subscribers keep good data.

The only exception handling here is the top-level catch that turns a known
``FatalError`` into a clean stderr message and exit 1. Unknown exceptions are
deliberately allowed to propagate as tracebacks.
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone

from . import build as build_mod
from . import state as state_mod
from .config import HEARTBEAT_PATH, ICS_PATH, PAGES_HOSTNAME, STATE_PATH
from .errors import AUTH_REMEDY, AuthError, FatalError
from .extract import extract_effective_date, extract_table_text
from .fetch import fetch_page
from .parse import parse_rows
from .validate import check_row_count, validate_rows


def validate_ics(text: str, expected_uids: list[str]) -> None:
    """Re-parse the generated calendar with ``icalendar`` and check it round-trips."""
    from icalendar import Calendar

    calendar = Calendar.from_ical(text)

    if calendar.name != "VCALENDAR":
        raise FatalError(f"Generated ICS did not round-trip: top component is {calendar.name!r}.")

    events = [component for component in calendar.walk("VEVENT")]
    if len(events) != len(expected_uids):
        raise FatalError(
            f"Generated ICS round-tripped to {len(events)} VEVENTs, expected "
            f"{len(expected_uids)}. Not writing it."
        )

    parsed_uids = [str(component.get("UID")) for component in events]
    if parsed_uids != expected_uids:
        raise FatalError("Generated ICS round-tripped with unexpected or reordered UIDs. Not writing it.")

    timezones = [component for component in calendar.walk("VTIMEZONE")]
    if len(timezones) != 1:
        raise FatalError(f"Generated ICS has {len(timezones)} VTIMEZONE blocks, expected exactly 1.")

    for component in events:
        for field in ("DTSTART", "DTEND"):
            value = component.get(field)
            if value is None:
                raise FatalError(f"A VEVENT is missing {field}. Not writing the calendar.")
            if value.params.get("TZID") != "America/Los_Angeles":
                raise FatalError(
                    f"A VEVENT has {field} without TZID=America/Los_Angeles "
                    f"(params: {dict(value.params)}). Refusing to publish floating or UTC times."
                )


def run() -> int:
    run_time = datetime.now(timezone.utc).replace(tzinfo=None)

    previous = state_mod.load_state(STATE_PATH)

    html = fetch_page()
    effective = extract_effective_date(html)
    table_text = extract_table_text(html)

    rows = parse_rows(table_text)
    events = validate_rows(rows, effective)
    check_row_count(len(events), previous.row_count)

    sequences = state_mod.assign_sequences(events, previous)
    last_modified = state_mod.assign_last_modified(events, sequences, previous, run_time)

    ics = build_mod.build_calendar(
        events, sequences, run_time, last_modified=last_modified, hostname=PAGES_HOSTNAME
    )

    expected_uids = [f"{event.uid_local}@{PAGES_HOSTNAME}" for event in events]
    validate_ics(ics, expected_uids)

    # --- everything above succeeded; only now does anything get written ---

    ICS_PATH.parent.mkdir(parents=True, exist_ok=True)
    ICS_PATH.write_text(ics, encoding="utf-8", newline="")

    new_state = state_mod.build_state(events, sequences, last_modified, run_time)
    state_mod.save_state(new_state, STATE_PATH)

    HEARTBEAT_PATH.parent.mkdir(parents=True, exist_ok=True)
    HEARTBEAT_PATH.write_text(run_time.strftime("%Y-%m-%dT%H:%M:%SZ") + "\n", encoding="utf-8")

    changed = sum(1 for event in events if sequences[event.uid_local] > 0)
    print(
        f"OK: effective {effective.isoformat()}, {len(events)} events "
        f"({changed} with a non-zero SEQUENCE), written to {ICS_PATH}."
    )
    return 0


def main() -> int:
    try:
        return run()
    except AuthError as exc:
        print("=" * 72, file=sys.stderr)
        print("FATAL: Claude Code authentication failed.", file=sys.stderr)
        print("=" * 72, file=sys.stderr)
        print(AUTH_REMEDY, file=sys.stderr)
        print(f"Underlying detail:\n{exc}", file=sys.stderr)
        return 1
    except FatalError as exc:
        print(f"FATAL: {exc}", file=sys.stderr)
        print(
            "\nNothing was written. The previously published calendar and state "
            "file are unchanged.",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    sys.exit(main())
