"""Pull the effective date and the schedule table out of the page HTML.

Both extractors match on *visible text*, not on Drupal CSS classes or node
structure. SFMTA can re-theme the site without breaking this; they cannot
rename the "Event Date" column heading without it being a real content change
that ought to fail loudly anyway.

Only the table's text is handed onward to the parse step -- never the whole
page.
"""

from __future__ import annotations

import re
from datetime import date, datetime

from bs4 import BeautifulSoup

from .errors import ExtractError

# "Tuesday, June 2, 2026" / "June 2, 2026" -- the weekday prefix is optional.
_EFFECTIVE_DATE_RE = re.compile(
    r"Effective\s+Date\b[\s:\u2013\u2014-]*"
    r"(?:[A-Z][a-z]+day\s*,\s*)?"
    r"([A-Z][a-z]+\s+\d{1,2},\s*\d{4})",
    re.IGNORECASE,
)

# Column headings that identify the schedule table among any other tables.
_REQUIRED_HEADINGS = ("event date", "venue")


def _normalize_space(text: str) -> str:
    return re.sub(r"[\s\u00a0]+", " ", text).strip()


def extract_effective_date(html: str) -> date:
    """Return the page's Effective Date, or raise ``ExtractError``."""
    soup = BeautifulSoup(html, "html.parser")
    text = _normalize_space(soup.get_text(" "))

    match = _EFFECTIVE_DATE_RE.search(text)
    if not match:
        raise ExtractError(
            "Could not locate an 'Effective Date' on the notice page.\n"
            "The year inference anchors on it, so there is nothing safe to fall "
            "back to. The page layout has probably changed."
        )

    raw = _normalize_space(match.group(1))
    for fmt in ("%B %d, %Y", "%b %d, %Y"):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue

    raise ExtractError(f"Found an Effective Date field but could not parse it: {raw!r}")


def extract_table_text(html: str) -> str:
    """Return the schedule table rendered as pipe-delimited text.

    Raises ``ExtractError`` if no table carries the expected headings, or if the
    matched table has no body rows at all.
    """
    soup = BeautifulSoup(html, "html.parser")
    tables = soup.find_all("table")

    if not tables:
        raise ExtractError(
            "No <table> element anywhere in the notice page. The schedule is "
            "published as an HTML table; if it is gone, the page changed shape."
        )

    for table in tables:
        rows = table.find_all("tr")
        if not rows:
            continue

        header_cells = [_normalize_space(c.get_text(" ")).lower() for c in rows[0].find_all(["th", "td"])]
        header_blob = " | ".join(header_cells)
        if not all(heading in header_blob for heading in _REQUIRED_HEADINGS):
            continue

        lines = []
        for row in rows:
            cells = [_normalize_space(c.get_text(" ")) for c in row.find_all(["th", "td"])]
            # The trailing empty row (and any other all-blank row) is dropped
            # here rather than being handed to the parse step as noise.
            if not any(cells):
                continue
            lines.append(" | ".join(cells))

        if len(lines) <= 1:
            raise ExtractError(
                "Located the schedule table but it contains no data rows beneath "
                "its header. Refusing to build an empty calendar."
            )

        return "\n".join(lines)

    raise ExtractError(
        f"Found {len(tables)} table(s) on the notice page, none of which has both "
        "an 'Event Date' and a 'Venue' column heading. The schedule table could "
        "not be identified."
    )
