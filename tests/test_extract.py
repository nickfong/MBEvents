"""Parser tests against the snapshotted notice page. No network."""

from __future__ import annotations

from datetime import date

import pytest

from sfmta_ics.errors import ExtractError
from sfmta_ics.extract import extract_effective_date, extract_table_text


def test_effective_date_is_read_from_the_page(notice_html):
    assert extract_effective_date(notice_html) == date(2026, 6, 2)


def test_missing_effective_date_raises():
    with pytest.raises(ExtractError, match="Effective Date"):
        extract_effective_date("<html><body><table><tr><th>Event Date</th></tr></table></body></html>")


def test_table_is_selected_by_its_headings_not_by_position(notice_html):
    # The fixture also contains a breadcrumb table, which must be ignored.
    text = extract_table_text(notice_html)
    assert text.splitlines()[0] == "Event Date | Venue | Special Event Rate Hours"
    assert "Getting Around" not in text


def test_trailing_blank_row_is_dropped(notice_html):
    lines = extract_table_text(notice_html).splitlines()
    assert len(lines) == 35, "34 data rows plus one header row"
    assert lines[-1] == "October 19 | Chase | 6 P.M. to 10 P.M."


def test_duplicate_dates_with_different_venues_are_both_kept(notice_html):
    lines = extract_table_text(notice_html).splitlines()
    july_10 = [line for line in lines if line.startswith("July 10 |")]
    assert july_10 == [
        "July 10 | Oracle | 6 P.M. to 10 P.M.",
        "July 10 | Chase | 6 P.M. to 10 P.M.",
    ]


def test_non_breaking_spaces_are_normalised(notice_html):
    text = extract_table_text(notice_html)
    assert " " not in text
    assert "June 10 | Oracle | Noon to 6 P.M." in text


def test_page_without_the_schedule_table_raises():
    html = """
    <html><body>
      <table><tr><th>Section</th><th>Link</th></tr><tr><td>a</td><td>b</td></tr></table>
    </body></html>
    """
    with pytest.raises(ExtractError, match="could not be identified"):
        extract_table_text(html)


def test_page_with_no_tables_at_all_raises():
    with pytest.raises(ExtractError, match="No <table>"):
        extract_table_text("<html><body><p>Nothing here.</p></body></html>")


def test_header_only_table_raises_rather_than_producing_nothing():
    html = """
    <html><body><table>
      <tr><th>Event Date</th><th>Venue</th><th>Special Event Rate Hours</th></tr>
      <tr><td></td><td></td><td></td></tr>
    </table></body></html>
    """
    with pytest.raises(ExtractError, match="no data rows"):
        extract_table_text(html)
