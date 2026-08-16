"""Turn the extracted table text into JSON, using the Claude Code CLI.

This is the *only* place a model is involved, and its job is purely mechanical
transcription. No date maths, no year inference, no time interpretation -- all
of that happens in ``validate.py`` where it can be unit tested.

The response is fed straight to ``json.loads``. If that raises, the run dies and
prints the raw output. There is no fence-stripping, no regex repair, no retry,
and no hand-rolled fallback parser: a model that started emitting prose is a
thing to find out about, not to paper over.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess

from .errors import AuthError, ParseError

CLI_TIMEOUT_SECONDS = 180

PROMPT = """\
You are transcribing one HTML table into JSON. This is a mechanical task.

The table text is on stdin. Its columns are: Event Date, Venue, Special Event
Rate Hours. The first line is the header row -- skip it.

Output a JSON array. One object per data row, in the order the rows appear:

  [{"date": "June 2", "venue": "Chase", "hours": "6 P.M. to 10 P.M."}]

Rules:
- Copy each cell's text verbatim. Do not normalise, expand, correct, or reformat
  anything. "Noon" stays "Noon". "6 P.M. to 10 P.M." keeps its periods.
- Do not add a year to the date. The dates have no year; leave them that way.
- Do not merge, deduplicate, reorder, or sort rows. The same date legitimately
  appears more than once with different venues; emit each occurrence separately.
- Skip rows whose cells are all empty. Skip nothing else.
- Do not invent, infer, or complete any row that is not in the input.

Output the JSON array and nothing else. No prose, no explanation, no markdown
code fences.
"""

# Substrings that mean "the credential was refused" rather than "the model
# produced something odd". Matched case-insensitively against the CLI's output.
_AUTH_MARKERS = (
    "oauth token",
    "authentication_error",
    "invalid api key",
    "invalid_api_key",
    "unauthorized",
    "401",
    "please run /login",
    "please run `claude login`",
    "credentials",
    "not logged in",
    "expired",
)


def _cli_environment() -> dict[str, str]:
    """Environment for the CLI: OAuth token only, API key forcibly absent.

    The workflow already unsets ``ANTHROPIC_API_KEY``. This is the second belt:
    if the key is ever present it silently takes precedence over the OAuth token
    and bills a different account, which is precisely the kind of quiet wrong
    behaviour this repo exists to avoid.
    """
    env = dict(os.environ)
    env.pop("ANTHROPIC_API_KEY", None)
    env.pop("ANTHROPIC_AUTH_TOKEN", None)

    token = env.get("CLAUDE_CODE_OAUTH_TOKEN", "").strip()
    if not token:
        raise AuthError("CLAUDE_CODE_OAUTH_TOKEN is not set in the environment.")

    return env


def _looks_like_auth_failure(output: str) -> bool:
    lowered = output.lower()
    return any(marker in lowered for marker in _AUTH_MARKERS)


def run_claude(table_text: str) -> str:
    """Run the Claude Code CLI headless over ``table_text``; return raw stdout."""
    env = _cli_environment()

    binary = shutil.which("claude")
    if binary is None:
        raise ParseError(
            "The 'claude' CLI is not on PATH. The workflow installs it with\n"
            "  npm install -g @anthropic-ai/claude-code\n"
            "before running this script."
        )

    try:
        completed = subprocess.run(
            [binary, "-p", PROMPT, "--output-format", "text"],
            input=table_text,
            capture_output=True,
            text=True,
            timeout=CLI_TIMEOUT_SECONDS,
            env=env,
        )
    except subprocess.TimeoutExpired as exc:
        raise ParseError(
            f"The Claude Code CLI did not return within {CLI_TIMEOUT_SECONDS}s."
        ) from exc

    combined = f"{completed.stdout}\n{completed.stderr}"

    if completed.returncode != 0:
        if _looks_like_auth_failure(combined):
            raise AuthError(
                f"The Claude Code CLI exited {completed.returncode} and the output "
                f"indicates a credential problem.\n\n"
                f"--- CLI output ---\n{combined.strip()}\n--- end ---"
            )
        raise ParseError(
            f"The Claude Code CLI exited {completed.returncode}.\n\n"
            f"--- CLI output ---\n{combined.strip()}\n--- end ---"
        )

    if not completed.stdout.strip():
        # A zero exit with no stdout is, in practice, almost always auth.
        raise AuthError(
            "The Claude Code CLI exited 0 but produced no output at all.\n"
            f"--- stderr ---\n{completed.stderr.strip()}\n--- end ---"
        )

    return completed.stdout


def parse_rows(table_text: str, runner=run_claude) -> list[dict[str, str]]:
    """Return the transcribed rows, or raise.

    ``runner`` is injected so the tests can exercise this without the CLI.
    """
    raw = runner(table_text)

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        if _looks_like_auth_failure(raw):
            raise AuthError(
                "The parse step returned an authentication message instead of JSON.\n\n"
                f"--- raw output ---\n{raw.strip()}\n--- end ---"
            ) from exc
        raise ParseError(
            f"The parse step did not return valid JSON ({exc}).\n"
            "Not attempting to repair it -- the raw output follows verbatim.\n\n"
            f"--- raw output ---\n{raw.strip()}\n--- end ---"
        ) from exc

    if not isinstance(data, list):
        raise ParseError(
            f"Expected a JSON array of rows, got {type(data).__name__}.\n\n"
            f"--- raw output ---\n{raw.strip()}\n--- end ---"
        )

    if not data:
        raise ParseError(
            "The parse step returned zero rows. The source table is never empty; "
            "refusing to build an empty calendar."
        )

    for index, row in enumerate(data):
        if not isinstance(row, dict):
            raise ParseError(f"Row {index} is {type(row).__name__}, expected an object: {row!r}")
        missing = [key for key in ("date", "venue", "hours") if key not in row]
        if missing:
            raise ParseError(f"Row {index} is missing required key(s) {missing}: {row!r}")
        for key in ("date", "venue", "hours"):
            if not isinstance(row[key], str):
                raise ParseError(
                    f"Row {index} has a non-string {key!r}: {row!r}\n"
                    "The model should transcribe cells as strings, blank ones included."
                )
            # A blank string is allowed through: SFMTA does occasionally publish
            # a row with an empty cell, and the validation layer quarantines
            # those explicitly rather than killing the whole publish here.

    return data
