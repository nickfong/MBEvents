"""Failure taxonomy.

Every fatal condition in this pipeline raises one of these. ``main`` catches
``FatalError`` only, prints the message to stderr, and exits 1. Anything that is
*not* a ``FatalError`` propagates as a traceback -- also a non-zero exit, but
loud about being a bug rather than a known failure mode.

There is deliberately no "warn and skip" path anywhere in this package.
"""

from __future__ import annotations


class FatalError(Exception):
    """A known failure mode. Exits 1 with the message on stderr."""


class FetchError(FatalError):
    """The source page could not be retrieved, or did not return 200."""


class ExtractError(FatalError):
    """The expected table or the Effective Date could not be located."""


class ParseError(FatalError):
    """The parse step returned something that is not the expected JSON."""


class ValidationError(FatalError):
    """A row, a date, a venue, an hours string, or the row count is unacceptable."""


class AuthError(FatalError):
    """The CLAUDE_CODE_OAUTH_TOKEN was missing or rejected.

    Separated from every other failure because the fix is specific and manual,
    and because it is the failure most likely to happen months from now with no
    other context. ``main`` prints this one as its own block.
    """


AUTH_REMEDY = """\
The Claude Code OAuth token was rejected or is missing.

  ACTION REQUIRED -- this will not fix itself:

    1. On your own machine, run:   claude setup-token
    2. Copy the token it prints.
    3. In this repository, go to:
         Settings -> Secrets and variables -> Actions -> CLAUDE_CODE_OAUTH_TOKEN
       and update the secret with the new value.
    4. Re-run this workflow from the Actions tab ("Run workflow").

  The calendar file was NOT modified. Subscribers keep the last good data
  until this is done.
"""
