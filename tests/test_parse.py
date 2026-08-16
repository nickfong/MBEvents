"""The parse step's contract: strict JSON in, or a loud failure."""

from __future__ import annotations

import json

import pytest

from sfmta_ics.errors import AuthError, ParseError
from sfmta_ics.parse import parse_rows


def stub(payload: str):
    return lambda _table_text: payload


def test_valid_json_passes_through(parsed_rows):
    assert parse_rows("irrelevant", runner=stub(json.dumps(parsed_rows))) == parsed_rows


def test_markdown_fences_are_not_stripped_they_are_a_failure(parsed_rows):
    fenced = "```json\n" + json.dumps(parsed_rows) + "\n```"
    with pytest.raises(ParseError, match="did not return valid JSON"):
        parse_rows("irrelevant", runner=stub(fenced))


def test_prose_is_a_failure_and_the_raw_output_is_printed():
    with pytest.raises(ParseError) as excinfo:
        parse_rows("irrelevant", runner=stub("Here is the table you asked for:"))
    assert "Here is the table you asked for" in str(excinfo.value)


def test_json_that_is_not_an_array_is_a_failure():
    with pytest.raises(ParseError, match="Expected a JSON array"):
        parse_rows("irrelevant", runner=stub('{"date": "June 2"}'))


def test_empty_array_is_a_failure_not_an_empty_calendar():
    with pytest.raises(ParseError, match="zero rows"):
        parse_rows("irrelevant", runner=stub("[]"))


def test_row_missing_a_key_is_a_failure():
    with pytest.raises(ParseError, match="missing required key"):
        parse_rows("irrelevant", runner=stub('[{"date": "June 2", "venue": "Chase"}]'))


def test_a_blank_cell_passes_through_for_the_validator_to_quarantine():
    # The parse layer transcribes; deciding what a blank cell means is the
    # validation layer's job (quarantine, carry-forward, or a capped failure).
    payload = '[{"date": "June 2", "venue": "Chase", "hours": ""}]'
    assert parse_rows("irrelevant", runner=stub(payload)) == [
        {"date": "June 2", "venue": "Chase", "hours": ""}
    ]


def test_a_non_string_field_is_still_fatal():
    payload = '[{"date": "June 2", "venue": "Chase", "hours": null}]'
    with pytest.raises(ParseError, match="non-string"):
        parse_rows("irrelevant", runner=stub(payload))


def test_row_that_is_not_an_object_is_a_failure():
    with pytest.raises(ParseError, match="expected an object"):
        parse_rows("irrelevant", runner=stub('["June 2 | Chase"]'))


def test_an_auth_message_is_reported_as_auth_not_as_bad_json():
    message = "Invalid API key · Please run /login"
    with pytest.raises(AuthError):
        parse_rows("irrelevant", runner=stub(message))
