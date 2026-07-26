"""Constants and paths. No behaviour lives here."""

from __future__ import annotations

import os
from pathlib import Path

# --- Source -----------------------------------------------------------------

SOURCE_URL = "https://www.sfmta.com/notices/current-special-event-parking-regulations-schedule"

HTTP_TIMEOUT_SECONDS = 30
USER_AGENT = "sfmta-parking-ics (+https://github.com/nickfong/mbevents)"

# --- Output -----------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parent.parent

ICS_PATH = REPO_ROOT / "docs" / "sfmta-events.ics"
STATE_PATH = REPO_ROOT / "state" / "events.json"
HEARTBEAT_PATH = REPO_ROOT / "state" / "last-run.txt"

# Hostname used in the UID right-hand side and in the subscription URL.
# Overridable so a fork does not silently inherit someone else's UID namespace.
PAGES_HOSTNAME = os.environ.get("PAGES_HOSTNAME", "nickfong.github.io")

CALENDAR_NAME = "SFMTA Special Event Parking"
PRODID = "-//nickfong/mbevents//SFMTA Special Event Parking//EN"
TIMEZONE_ID = "America/Los_Angeles"

# --- Domain whitelists ------------------------------------------------------

# Venue token in the table -> display name used in SUMMARY/DESCRIPTION.
VENUES = {
    "Oracle": "Oracle Park",
    "Chase": "Chase Center",
}

# Normalised hours string -> (start hour, end hour), local wall time.
# Deliberately a closed whitelist: a new SFMTA window must be added by hand.
HOURS_WINDOWS = {
    "noon to 6 p.m.": (12, 18),
    "3 p.m. to 10 p.m.": (15, 22),
    "6 p.m. to 10 p.m.": (18, 22),
}

# (start hour, end hour) -> the string as SFMTA writes it. Used to re-emit an
# archived event whose state record predates the stored hours text.
HOURS_TEXT = {
    (12, 18): "Noon to 6 P.M.",
    (15, 22): "3 P.M. to 10 P.M.",
    (18, 22): "6 P.M. to 10 P.M.",
}

# --- Validation thresholds --------------------------------------------------

# A run whose row count fell by more than this fraction of the last successful
# run is treated as a broken source, not a short schedule.
MAX_ROW_COUNT_DROP = 0.40

# Every inferred date must land inside [effective - 30d, effective + 400d].
SANE_WINDOW_DAYS_BEFORE = 30
SANE_WINDOW_DAYS_AFTER = 400
