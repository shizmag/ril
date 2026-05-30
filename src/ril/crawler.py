import asyncio
import logging
from playwright.async_api import async_playwright
from playwright_stealth import Stealth
from ril.config import CRAWLER_HEADLESS, CRAWLER_STEALTH, CRAWLER_TIMEOUT_MS

logger = logging.getLogger(__name__)

async def trigger_lazy_loading(page) -> None:
    """Scroll down the page incrementally to trigger lazy-loaded images."""
    try:
        # Scroll down in 5 incremental chunks to trigger viewport events
        for i in range(1, 6):
            await page.evaluate(f"window.scrollTo(0, document.body.scrollHeight * {i} / 5)")
            await asyncio.sleep(0.25)
        # Scroll back to the top
        await page.evaluate("window.scrollTo(0, 0)")
        await asyncio.sleep(0.2)
    except Exception as e:
        logger.warning(f"Error triggering lazy loading: {e}")


async def fetch_html(
    url: str,
    headless: bool = CRAWLER_HEADLESS,
    stealth: bool = CRAWLER_STEALTH,
    timeout_ms: int = CRAWLER_TIMEOUT_MS
) -> str:
    """
    Fetch raw HTML from a URL using Playwright.
    Bypasses JS rendering issues and basic anti-scraping blocks.
    """
    logger.info(f"Crawling URL: {url}")
    
    async with async_playwright() as p:
        # Launch browser
        browser = await p.chromium.launch(headless=headless)
        
        # Create a new context with a standard desktop viewport and User-Agent
        user_agent = (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/122.0.0.0 Safari/537.36"
        )
        context = await browser.new_context(
            user_agent=user_agent,
            viewport={"width": 1280, "height": 800},
            accept_downloads=False
        )
        
        page = await context.new_page()
        
        # Apply stealth scripts to prevent detection as a automation tool
        if stealth:
            stealth_obj = Stealth()
            await stealth_obj.apply_stealth_async(page)
            
        try:
            # Navigate to the page
            # We try 'networkidle' but fall back to 'domcontentloaded' or 'load' on failure/timeout
            try:
                await page.goto(url, timeout=timeout_ms, wait_until="networkidle")
            except Exception as e:
                logger.warning(f"Timeout or error waiting for networkidle, trying load: {e}")
                await page.goto(url, timeout=timeout_ms, wait_until="load")
                
            # Trigger lazy loading of images
            await trigger_lazy_loading(page)
            
            # Get the complete rendered HTML content
            html = await page.content()
            return html
            
        except Exception as e:
            logger.error(f"Failed to fetch page {url}: {e}")
            raise e
        finally:
            await context.close()
            browser_close_task = browser.close()
            # Wait for browser to close properly
            await asyncio.wait_for(browser_close_task, timeout=5.0)

