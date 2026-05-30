import logging
from readability import Document

logger = logging.getLogger(__name__)

def extract_article(html_content: str) -> tuple[str, str]:
    """
    Extract the main article title and clean HTML content from raw HTML.
    Returns:
        tuple[title, cleaned_html]
    """
    try:
        # Document parses the html and determines the core content area
        doc = Document(html_content)
        title = doc.title()
        
        # doc.summary() returns the clean, boiled-down HTML of the article
        cleaned_html = doc.summary()
        
        # Basic sanity check
        if not title:
            title = "Untitled Article"
            
        return title, cleaned_html
        
    except Exception as e:
        logger.error(f"Readability extraction failed: {e}")
        # Return fallback values
        return "Extraction Failed", html_content
