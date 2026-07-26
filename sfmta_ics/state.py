"""The committed state file: the archive, SEQUENCE bookkeeping, and row counts.

``state/events.json`` is the durable record. Each UID keeps the start, end,
venue, hours, SEQUENCE and LAST-MODIFIED it was last published with -- enough to
re-emit the event without re-scraping.

SFMTA only publishes a rolling window, so dates drop off the page once they are
past. Rebuilding purely from the page would delete that history from
subscribers' calendars. Instead:

  - a scraped UID whose start or end changed gets its sequence incremented,
    which is what makes a client update the event in place;
  - a scraped UID whose times are unchanged keeps its sequence;
  - a UID not seen before starts at 0;
  - a UID that has left the page and is **in the past** is archived: frozen at
    its last known values and re-emitted forever;
  - a UID that has left the page and is **still in the future** is dropped. That
    is a cancellation or a reschedule, and it should leave the calendar.

Freezing matters: a retroactive SFMTA edit to a finished date cannot rewrite
history, because archived events are never re-derived from the page.

The file is only written after the ICS has been built and validated, so a failed
run leaves both the state and the previously published calendar untouched.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from .config import HOURS_TEXT, STATE_PATH, VENUES
from .errors import FatalError
from .validate import Event

STATE_VERSION = 2


@dataclass
class State:
    events: dict[str, dict]           # uid -> {"start", "end", "sequence"}
    row_count: int | None = None      # rows in the last successful run
    last_success_utc: str | None = None

    @classmethod
    def empty(cls) -> "State":
        return cls(events={}, row_count=None, last_success_utc=None)


def load_state(path: Path = STATE_PATH) -> State:
    """Read the state file. A missing file is a first run, not an error."""
    if not path.exists():
        return State.empty()

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        # Not recoverable by guessing: silently starting from empty would reset
        # every SEQUENCE to 0 and strand revisions in subscribers' calendars.
        raise FatalError(
            f"{path} exists but is not valid JSON ({exc}).\n"
            "Refusing to start from an empty state: that would reset every "
            "SEQUENCE to 0 and stop clients from picking up revisions.\n"
            "Fix or delete the file deliberately."
        ) from exc

    if not isinstance(raw, dict) or "events" not in raw:
        raise FatalError(
            f"{path} is valid JSON but not in the expected shape "
            '(an object with an "events" key).'
        )

    events = raw["events"]
    if not isinstance(events, dict):
        raise FatalError(f'{path}: "events" must be an object mapping UID -> record.')

    for uid, record in events.items():
        if not isinstance(record, dict) or not {"start", "end", "sequence"} <= set(record):
            raise FatalError(
                f'{path}: entry {uid!r} must have "start", "end" and "sequence"; got {record!r}.'
            )

    return State(
        events=events,
        row_count=raw.get("row_count"),
        last_success_utc=raw.get("last_success_utc"),
    )


def assign_sequences(events, previous: State) -> dict[str, int]:
    """Return ``uid -> SEQUENCE`` for this run's events."""
    sequences: dict[str, int] = {}

    for event in events:
        uid = event.uid_local
        start = event.start.isoformat()
        end = event.end.isoformat()

        record = previous.events.get(uid)
        if record is None:
            sequences[uid] = 0
        elif record["start"] != start or record["end"] != end:
            sequences[uid] = int(record["sequence"]) + 1
        else:
            sequences[uid] = int(record["sequence"])

    return sequences


def assign_last_modified(
    events, sequences: dict[str, int], previous: State, run_time: datetime
) -> dict[str, str]:
    """Return ``uid -> LAST-MODIFIED`` stamp.

    An event that did not change keeps the stamp it was published with, so
    LAST-MODIFIED means what it says instead of just echoing the run time.
    """
    stamp = run_time.strftime("%Y%m%dT%H%M%SZ")
    result: dict[str, str] = {}

    for event in events:
        uid = event.uid_local
        record = previous.events.get(uid)
        unchanged = (
            record is not None
            and record["start"] == event.start.isoformat()
            and record["end"] == event.end.isoformat()
            and int(record["sequence"]) == sequences[uid]
        )
        result[uid] = record.get("last_modified", stamp) if unchanged else stamp

    return result


def _event_from_record(uid: str, record: dict) -> Event:
    """Rebuild an ``Event`` from a stored record, without re-scraping."""
    try:
        start = datetime.fromisoformat(record["start"])
        end = datetime.fromisoformat(record["end"])
    except (TypeError, ValueError) as exc:
        raise FatalError(f"Archived entry {uid!r} has an unparseable start/end: {record!r}") from exc

    # Records written before the archive existed carry neither venue nor hours.
    # Both are recoverable: the venue from the UID, the hours from the window.
    venue = record.get("venue") or uid.rsplit("-", 1)[-1].capitalize()
    if venue not in VENUES:
        raise FatalError(
            f"Archived entry {uid!r} has venue {venue!r}, which is not one of "
            f"{sorted(VENUES)}. Refusing to re-emit it."
        )

    hours_text = record.get("hours") or HOURS_TEXT.get((start.hour, end.hour))
    if not hours_text:
        raise FatalError(
            f"Archived entry {uid!r} has no stored hours text and its window "
            f"({start.hour}:00-{end.hour}:00) is not a known one. Refusing to guess."
        )

    return Event(
        event_date=start.date(),
        venue=venue,
        venue_display=VENUES[venue],
        hours_text=hours_text,
        start_hour=start.hour,
        end_hour=end.hour,
    )


def archived_events(previous: State, scraped_uids: set[str], today: date) -> list[Event]:
    """Past events that have left the page and should still be published.

    A UID missing from the scrape is archived only if its date is already past.
    A future one is a cancellation and is allowed to disappear.
    """
    archived = []
    for uid, record in sorted(previous.events.items()):
        if uid in scraped_uids:
            continue
        # Build first, so a malformed record fails with a clear message rather
        # than a raw ValueError out of the date comparison.
        event = _event_from_record(uid, record)
        if event.event_date < today:
            archived.append(event)
    return archived


def build_state(
    events,
    sequences: dict[str, int],
    last_modified: dict[str, str],
    run_time: datetime,
    scraped_count: int,
) -> State:
    """Assemble the state to persist.

    ``events`` is the full published set, archive included. ``scraped_count`` is
    the number of rows read from the page this run, and is what the row-count
    guard compares against -- a growing archive must never mask a scrape that
    collapsed.
    """
    return State(
        events={
            event.uid_local: {
                "start": event.start.isoformat(),
                "end": event.end.isoformat(),
                "venue": event.venue,
                "hours": event.hours_text,
                "sequence": sequences[event.uid_local],
                "last_modified": last_modified[event.uid_local],
            }
            for event in events
        },
        row_count=scraped_count,
        last_success_utc=run_time.strftime("%Y-%m-%dT%H:%M:%SZ"),
    )


def save_state(state: State, path: Path = STATE_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": STATE_VERSION,
        "last_success_utc": state.last_success_utc,
        "row_count": state.row_count,
        "events": dict(sorted(state.events.items())),
    }
    path.write_text(json.dumps(payload, indent=2, sort_keys=False) + "\n", encoding="utf-8")
