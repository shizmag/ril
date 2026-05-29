import pytest
from unittest.mock import AsyncMock, MagicMock
from ril import crawler

@pytest.mark.asyncio
async def test_fetch_html_success(mocker):
    # Mock Page
    mock_page = AsyncMock()
    mock_page.content = AsyncMock(return_value="<html>Mocked content</html>")
    mock_page.goto = AsyncMock()
    
    # Mock Context
    mock_context = AsyncMock()
    mock_context.new_page = AsyncMock(return_value=mock_page)
    mock_context.close = AsyncMock()
    
    # Mock Browser
    mock_browser = AsyncMock()
    mock_browser.new_context = AsyncMock(return_value=mock_context)
    mock_browser.close = AsyncMock()
    
    # Mock Chromium type
    mock_chromium = MagicMock()
    mock_chromium.launch = AsyncMock(return_value=mock_browser)
    
    # Mock Playwright Object
    mock_playwright = MagicMock()
    mock_playwright.chromium = mock_chromium
    
    # Mock the async_playwright function itself
    mock_ap = mocker.patch("ril.crawler.async_playwright")
    
    # Handle the async context manager (__aenter__ / __aexit__)
    mock_ap_context = AsyncMock()
    mock_ap_context.__aenter__.return_value = mock_playwright
    mock_ap.return_value = mock_ap_context
    
    # Mock stealth
    mock_stealth = mocker.patch("playwright_stealth.Stealth.apply_stealth_async", new_callable=AsyncMock)
    
    # Call the crawler
    html = await crawler.fetch_html("https://example.com", stealth=True)
    
    assert html == "<html>Mocked content</html>"
    mock_chromium.launch.assert_called_once_with(headless=True)
    mock_page.goto.assert_called_once_with("https://example.com", timeout=30000, wait_until="networkidle")
    mock_stealth.assert_called_once_with(mock_page)
    mock_context.close.assert_called_once()
    mock_browser.close.assert_called_once()

@pytest.mark.asyncio
async def test_fetch_html_fallback(mocker):
    # Mock Page
    mock_page = AsyncMock()
    mock_page.content = AsyncMock(return_value="<html>Fallback content</html>")
    
    # Mock page.goto to throw on first call and succeed on second
    mock_page.goto = AsyncMock(side_effect=[Exception("Network Idle Timeout"), None])
    
    # Mock Context & Browser
    mock_context = AsyncMock()
    mock_context.new_page = AsyncMock(return_value=mock_page)
    mock_context.close = AsyncMock()
    
    mock_browser = AsyncMock()
    mock_browser.new_context = AsyncMock(return_value=mock_context)
    mock_browser.close = AsyncMock()
    
    mock_chromium = MagicMock()
    mock_chromium.launch = AsyncMock(return_value=mock_browser)
    
    mock_playwright = MagicMock()
    mock_playwright.chromium = mock_chromium
    
    # Mock async_playwright
    mock_ap = mocker.patch("ril.crawler.async_playwright")
    mock_ap_context = AsyncMock()
    mock_ap_context.__aenter__.return_value = mock_playwright
    mock_ap.return_value = mock_ap_context
    
    # Run with stealth=False
    html = await crawler.fetch_html("https://example.com", stealth=False)
    
    assert html == "<html>Fallback content</html>"
    # Verified it fell back from networkidle to load
    assert mock_page.goto.call_count == 2
    mock_page.goto.assert_any_call("https://example.com", timeout=30000, wait_until="networkidle")
    mock_page.goto.assert_any_call("https://example.com", timeout=30000, wait_until="load")
