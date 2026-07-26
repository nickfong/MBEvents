"""Render validated events into an RFC 5545 calendar.

Written by hand rather than through a library so that the VTIMEZONE block,
the REFRESH-INTERVAL hints and the exact UID form are all under direct control.
The result is then handed to ``icalendar`` for validation *before* it is allowed
anywhere near the disk -- see ``main.py``.
"""

from __future__ import annotations

from datetime import datetime

from .config import CALENDAR_NAME, PAGES_HOSTNAME, PRODID, SOURCE_URL, TIMEZONE_ID
from .validate import Event

CRLF = "\r\n"

# A real VTIMEZONE, so clients resolve the wall-clock times themselves. The
# alternative -- floating times, or pre-converting to UTC -- breaks the moment a
# client is in another timezone or the US changes its DST rules.
VTIMEZONE = """\
BEGIN:VTIMEZONE
TZID:America/Los_Angeles
X-LIC-LOCATION:America/Los_Angeles
BEGIN:DAYLIGHT
TZOFFSETFROM:-0800
TZOFFSETTO:-0700
TZNAME:PDT
DTSTART:19700308T020000
RRULE:FREQ=YEARLY;BYMONTH=3;BYDAY=2SU
END:DAYLIGHT
BEGIN:STANDARD
TZOFFSETFROM:-0700
TZOFFSETTO:-0800
TZNAME:PST
DTSTART:19701101T020000
RRULE:FREQ=YEARLY;BYMONTH=11;BYDAY=1SU
END:STANDARD
END:VTIMEZONE"""


def escape_text(value: str) -> str:
    """Escape a TEXT value per RFC 5545 section 3.3.11."""
    return (
        value.replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\n", "\\n")
    )


def fold(line: str) -> str:
    """Fold a content line to 75 octets, continuing with a leading space."""
    encoded = line.encode("utf-8")
    if len(encoded) <= 75:
        return line

    chunks = []
    remaining = encoded
    limit = 75
    while len(remaining) > limit:
        cut = limit
        # Never split a multi-byte character across the fold.
        while cut > 0 and (remaining[cut] & 0xC0) == 0x80:
            cut -= 1
        chunks.append(remaining[:cut].decode("utf-8"))
        remaining = remaining[cut:]
        limit = 74  # subsequent lines carry a leading space
    chunks.append(remaining.decode("utf-8"))

    return CRLF.join([chunks[0]] + [" " + chunk for chunk in chunks[1:]])


def _local(value: datetime) -> str:
    return value.strftime("%Y%m%dT%H%M%S")


def _utc(value: datetime) -> str:
    return value.strftime("%Y%m%dT%H%M%SZ")


def build_description(event: Event) -> str:
    return (
        f"Special event parking meter rates are in effect {event.hours_text} "
        f"near {event.venue_display}.\n"
        "\n"
        "This is the meter enforcement window, not the event start time.\n"
        "\n"
        "Dates and rate hours are subject to change by SFMTA.\n"
        f"Source: {SOURCE_URL}"
    )


def build_calendar(
    events: list[Event],
    sequences: dict[str, int],
    run_time: datetime,
    last_modified: dict[str, str] | None = None,
    hostname: str = PAGES_HOSTNAME,
) -> str:
    """Return the complete ICS document as a string.

    Built entirely in memory. The caller validates it and only then writes it.
    """
    if not events:
        # Belt and braces: nothing upstream should ever get here with no events,
        # and if something does, an empty calendar must not be produced.
        raise ValueError("build_calendar received zero events; refusing to emit an empty calendar.")

    stamp = _utc(run_time)
    scraped = run_time.strftime("%Y-%m-%d %H:%M UTC")
    last_modified = last_modified or {}

    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        f"PRODID:{PRODID}",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        f"X-WR-CALNAME:{escape_text(CALENDAR_NAME)}",
        "X-WR-CALDESC:"
        + escape_text(
            "Elevated parking meter rates near Oracle Park and Chase Center "
            "during large events. Meter enforcement windows, not event times. "
            f"Last successful scrape: {scraped}. Source: {SOURCE_URL}"
        ),
        f"X-WR-TIMEZONE:{TIMEZONE_ID}",
        "REFRESH-INTERVAL;VALUE=DURATION:PT12H",
        "X-PUBLISHED-TTL:PT12H",
        *VTIMEZONE.split("\n"),
    ]

    for event in events:
        uid = f"{event.uid_local}@{hostname}"
        lines.extend(
            [
                "BEGIN:VEVENT",
                f"UID:{uid}",
                f"DTSTAMP:{stamp}",
                f"LAST-MODIFIED:{last_modified.get(event.uid_local, stamp)}",
                f"SEQUENCE:{sequences[event.uid_local]}",
                f"DTSTART;TZID={TIMEZONE_ID}:{_local(event.start)}",
                f"DTEND;TZID={TIMEZONE_ID}:{_local(event.end)}",
                f"SUMMARY:{escape_text(f'Special event meter rates: {event.venue_display}')}",
                f"DESCRIPTION:{escape_text(build_description(event))}",
                f"LOCATION:{escape_text(event.venue_display)}",
                "TRANSP:TRANSPARENT",
                "STATUS:CONFIRMED",
                "END:VEVENT",
            ]
        )

    lines.append("END:VCALENDAR")

    return CRLF.join(fold(line) for line in lines) + CRLF
