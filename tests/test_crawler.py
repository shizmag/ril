import pytest
from unittest.mock import AsyncMock, MagicMock
from ril import crawler

@pytest.mark.asyncio
async def test_fetch_html_success(mocker):
    # Mock Page
    mock_page = AsyncMock()
    mock_page.content = AsyncMock(return_value="<html>Mocked content</html>")
    mock_page.goto = AsyncMock()
    
    # Configure page.locator to return a mock locator that has async methods
    mock_locator = MagicMock()
    mock_locator.count = AsyncMock(return_value=0)
    mock_locator.first = MagicMock()
    mock_locator.first.is_visible = AsyncMock(return_value=False)
    mock_locator.first.click = AsyncMock()
    mock_locator.nth = MagicMock(return_value=mock_locator)
    mock_page.locator = MagicMock(return_value=mock_locator)
    
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
    mock_page.goto.assert_called_once_with("https://example.com", timeout=30000, wait_until="load")
    mock_stealth.assert_called_once_with(mock_page)
    mock_context.close.assert_called_once()
    mock_browser.close.assert_called_once()

@pytest.mark.asyncio
async def test_fetch_html_fallback(mocker):
    # Mock Page
    mock_page = AsyncMock()
    mock_page.content = AsyncMock(return_value="<html>Fallback content</html>")
    
    # Mock page.goto to throw on call
    mock_page.goto = AsyncMock(side_effect=Exception("Load Timeout"))
    
    # Configure page.locator to return a mock locator that has async methods
    mock_locator = MagicMock()
    mock_locator.count = AsyncMock(return_value=0)
    mock_locator.first = MagicMock()
    mock_locator.first.is_visible = AsyncMock(return_value=False)
    mock_locator.first.click = AsyncMock()
    mock_locator.nth = MagicMock(return_value=mock_locator)
    mock_page.locator = MagicMock(return_value=mock_locator)
    
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
    # Verified it tried to go to page but proceeded on error without reloading
    assert mock_page.goto.call_count == 1
    mock_page.goto.assert_called_once_with("https://example.com", timeout=30000, wait_until="load")
