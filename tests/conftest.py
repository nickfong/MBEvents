from __future__ import annotations

import json
import sys
from datetime import date, datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

FIXTURES = Path(__file__).resolve().parent / "fixtures"


@pytest.fixture
def notice_html() -> str:
    return (FIXTURES / "notice-page.html").read_text(encoding="utf-8")


@pytest.fixture
def parsed_rows() -> list[dict[str, str]]:
    return json.loads((FIXTURES / "parsed-rows.json").read_text(encoding="utf-8"))


@pytest.fixture
def effective_date() -> date:
    return date(2026, 6, 2)


@pytest.fixture
def run_time() -> datetime:
    return datetime(2026, 6, 3, 13, 10, 0)
