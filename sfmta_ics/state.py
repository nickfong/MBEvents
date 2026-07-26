"""The committed state file: SEQUENCE bookkeeping and the previous row count.

``state/events.json`` maps each UID to the start, end, and SEQUENCE that were
last *published*. On each run:

  - a UID whose start or end changed gets its sequence incremented, which is
    what makes a calendar client update the event in place rather than ignore
    the revision;
  - a UID whose times are unchanged keeps its sequence;
  - a UID not seen before starts at 0;
  - a UID that vanished from the page is pruned. It simply will not appear in
    the republished ICS, and clients re-fetch the whole file.

The file is only written after the ICS has been built and validated, so a failed
run leaves both the state and the previously published calendar untouched.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .config import STATE_PATH
from .errors import FatalError

STATE_VERSION = 1


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


def build_state(
    events, sequences: dict[str, int], last_modified: dict[str, str], run_time: datetime
) -> State:
    """Assemble the state to persist. UIDs absent from ``events`` are pruned."""
    return State(
        events={
            event.uid_local: {
                "start": event.start.isoformat(),
                "end": event.end.isoformat(),
                "sequence": sequences[event.uid_local],
                "last_modified": last_modified[event.uid_local],
            }
            for event in events
        },
        row_count=len(events),
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
