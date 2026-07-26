"""Retrieve the source page.

One retry, for a genuinely transient network error only. A non-200 response is
not retried: SFMTA answering 404 twice is not more informative than once, and
retrying a persistently broken source is exactly the masking behaviour this
pipeline is supposed to avoid.
"""

from __future__ import annotations

import time

import requests

from .config import HTTP_TIMEOUT_SECONDS, SOURCE_URL, USER_AGENT
from .errors import FetchError

_RETRY_DELAY_SECONDS = 5


def fetch_page(url: str = SOURCE_URL) -> str:
    """Return the page HTML, or raise ``FetchError``."""
    headers = {"User-Agent": USER_AGENT}

    try:
        response = requests.get(url, headers=headers, timeout=HTTP_TIMEOUT_SECONDS)
    except requests.RequestException as first_error:
        # Transient: DNS blip, connection reset, read timeout. Exactly one retry.
        time.sleep(_RETRY_DELAY_SECONDS)
        try:
            response = requests.get(url, headers=headers, timeout=HTTP_TIMEOUT_SECONDS)
        except requests.RequestException as second_error:
            raise FetchError(
                f"Could not fetch {url} -- two consecutive network failures.\n"
                f"  first attempt:  {first_error!r}\n"
                f"  second attempt: {second_error!r}"
            ) from second_error

    if response.status_code != 200:
        raise FetchError(
            f"Fetching {url} returned HTTP {response.status_code} "
            f"({len(response.content)} bytes). Expected 200.\n"
            "The notice page may have been moved, renamed, or withdrawn."
        )

    if not response.text.strip():
        raise FetchError(f"Fetching {url} returned HTTP 200 with an empty body.")

    return response.text
