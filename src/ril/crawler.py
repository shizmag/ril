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


async def dismiss_cookie_consent(page) -> None:
    """
    Attempt to click 'Accept All', 'Agree', or 'OK' buttons on cookie consent banners
    to allow the main content of the page to load/render correctly.
    """
    accept_texts = [
        "Accept All", "Accept all", "Accept", "Agree", "I agree", 
        "Allow All", "Allow all", "Allow", "OK", "I accept", 
        "Принять всё", "Принять", "Согласен", "Разрешить всё", "Разрешить",
        "Yes, I agree", "Yes, agree", "Accept Cookies", "Accept cookies"
    ]
    
    common_selectors = [
        "#onetrust-accept-btn-handler",
        ".onetrust-close-btn-handler",
        "#didomi-notice-agree-button",
        "#CybotCookiebotDialogBodyLevelButtonLevelOptinAllowAll",
        "#CybotCookiebotDialogBodyLevelButtonLevelOptinAllowall",
        "#cookie-accept",
        ".cookie-accept",
        "[class*='accept-button']",
        "[id*='accept-button']",
        ".js-accept-cookies",
        "#js-accept-cookies"
    ]
    
    try:
        # 1. Try to click specific common selectors first
        for selector in common_selectors:
            locator = page.locator(selector)
            try:
                count = await locator.count()
                if not isinstance(count, int):
                    count = 0
            except Exception:
                count = 0

            if count > 0:
                element = locator.first
                try:
                    visible = await element.is_visible()
                    if not isinstance(visible, bool):
                        visible = False
                except Exception:
                    visible = False

                if visible:
                    logger.info(f"Clicking cookie consent button by selector: {selector}")
                    await element.click()
                    await asyncio.sleep(0.5)
                    return
                    
        # 2. Try to find buttons/links by text
        for text in accept_texts:
            locator = page.locator(f"button:has-text('{text}'), a:has-text('{text}')")
            try:
                count = await locator.count()
                if not isinstance(count, int):
                    count = 0
            except Exception:
                count = 0

            if count > 0:
                for i in range(count):
                    loc = locator.nth(i)
                    try:
                        visible = await loc.is_visible()
                        if not isinstance(visible, bool):
                            visible = False
                    except Exception:
                        visible = False

                    if visible:
                        logger.info(f"Clicking cookie consent button with text: '{text}'")
                        await loc.click()
                        await asyncio.sleep(0.5)
                        return
    except Exception as e:
        logger.debug(f"Error attempting to dismiss cookie consent: {e}")


async def intercept_route(route):
    """Intercept network requests and block known CMP, analytics, and cookie consent domains."""
    url = route.request.url.lower()
    block_patterns = [
        "onetrust.com", "cookiebot.com", "didomi.io", "quantcast.mgr.consensu.org",
        "secureserver.net", "trustarc.com", "consentmanager", "iab-tcf",
        "consensu.org", "evidon.com", "usercentrics.com", "cookiechoices",
        "cookiebanner", "cc.exoclick.com", "optanon", "privacy-notice",
        "cookie-law-info", "cookie-consent", "gdpr-consent"
    ]
    if any(pattern in url for pattern in block_patterns):
        logger.info(f"Blocked cookie/CMP network request: {url}")
        try:
            await route.abort()
        except Exception:
            pass
    else:
        try:
            await route.continue_()
        except Exception:
            pass


async def restore_scrolling_and_hide_overlays(page) -> None:
    """Inject CSS to hide any overlays/consent modals and restore body scrollability."""
    style_content = """
    #onetrust-consent-sdk, #onetrust-banner-sdk, .onetrust-pc-dark,
    #didomi-host, .didomi-popup, .didomi-consent-popup,
    #CybotCookiebotDialog, #cookiebot,
    #qc-cmp2-container, #qc-cmp2-ui,
    #consent_blackbar, #truste-consent-track,
    .cookie-consent, .cookieconsent, .cc-window, .cc-banner, .cc-type-info,
    #cookie-law-info-bar, #cookie-law-info-again,
    #sp-consent-container, .cookie-notice-container, .cookie-notice,
    #gdpr-consent-tool-wrapper, #gdpr-consent-banner,
    .cookie-banner, .cookie-popup, .cookie-dialog, .cookie-bar, .cookiebar,
    #privacy-consent, #cookie-consent-banner, .js-cookie-consent,
    [id*="consent-wall"], [class*="consent-wall"],
    [id*="cookie-wall"], [class*="cookie-wall"],
    .consent-dialog, .cookie-dialog, [role="dialog"], [role="alertdialog"],
    .modal, .modal-backdrop, .overlay, .popup-backdrop {
        display: none !important;
        visibility: hidden !important;
        opacity: 0 !important;
        pointer-events: none !important;
        height: 0 !important;
        width: 0 !important;
    }
    
    html, body {
        overflow: auto !important;
        overflow-y: auto !important;
        position: static !important;
        max-height: none !important;
        height: auto !important;
        user-select: auto !important;
    }
    """
    try:
        await page.evaluate(f"""
            const style = document.createElement('style');
            style.innerHTML = `{style_content}`;
            document.head.appendChild(style);
        """)
    except Exception as e:
        logger.debug(f"Failed to inject scroll restoration styles: {e}")


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
        
        # Block cookie consent banners & tracker scripts at the network level
        try:
            await page.route("**/*", intercept_route)
        except Exception:
            pass
        
        # Apply stealth scripts to prevent detection as a automation tool
        if stealth:
            stealth_obj = Stealth()
            await stealth_obj.apply_stealth_async(page)
            
        try:
            # Navigate to the page
            # Use 'load' instead of 'networkidle' to avoid hanging on sites with active background connections (trackers, analytics, etc.)
            try:
                await page.goto(url, timeout=timeout_ms, wait_until="load")
            except Exception as e:
                logger.warning(f"Timeout or error waiting for page load: {e}. Attempting to proceed with current DOM content.")
                
            # Dismiss cookie consent overlays if present (clicks any remaining buttons)
            await dismiss_cookie_consent(page)
            
            # Force restore scrolling and hide overlays via CSS injection
            await restore_scrolling_and_hide_overlays(page)
            
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

