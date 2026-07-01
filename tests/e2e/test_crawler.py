"""
Optional e2e tests for Playwright crawler.

These tests launch a real browser against real URLs.
SKIPPED by default. Only run when RIL_PLAYWRIGHT_E2E=1.

Usage:
    RIL_PLAYWRIGHT_E2E=1 python -m pytest tests/e2e/test_crawler.py -v
"""
import os
import pytest


PLAYWRIGHT_E2E = os.getenv("RIL_PLAYWRIGHT_E2E") == "1"

skip_playwright = pytest.mark.skipif(
    not PLAYWRIGHT_E2E,
    reason="Playwright e2e requires network and browser. Set RIL_PLAYWRIGHT_E2E=1 to run."
)


@skip_playwright
@pytest.mark.asyncio
async def test_fetch_html_real_page():
    """Fetch a real public page via Playwright."""
    from ril.crawler import fetch_html

    html = await fetch_html("https://example.com")
    assert "<html" in html.lower()
    assert "example" in html.lower()
