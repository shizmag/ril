import logging
import re
from bs4 import BeautifulSoup
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
        raw_title = doc.title()
        
        # doc.summary() returns the clean, boiled-down HTML of the article
        cleaned_html = doc.summary()
        
        title = None
        if html_content:
            soup = BeautifulSoup(html_content, "lxml")
            # Try to find the first heading (h1, h2, h3)
            for tag_name in ["h1", "h2", "h3"]:
                tag = soup.find(tag_name)
                if tag:
                    text = tag.get_text().strip()
                    if text:
                        title = text
                        break
        
        if not title:
            title = raw_title
            
        if not title:
            title = "Untitled Article"
        else:
            # Clean up extra spaces/newlines
            title = re.sub(r'\s+', ' ', title).strip()
            
            # Split before special characters (colon, semicolon, pipe, dot-space/dot-end, space-dash-space)
            match = re.search(r'[:;|]|\.(?:\s|$)|(?:\s+[-–—]\s+)', title)
            if match:
                title = title[:match.start()].strip()
                
            # Limit length to 80 characters, truncating at a word boundary
            LIMIT = 80
            if len(title) > LIMIT:
                truncated = title[:LIMIT].strip()
                if ' ' in truncated:
                    title = truncated.rsplit(' ', 1)[0].strip()
                else:
                    title = truncated
                    
            if len(title) < 3:
                title = raw_title or "Untitled Article"
            
        return title, cleaned_html
        
    except Exception as e:
        logger.error(f"Readability extraction failed: {e}")
        # Return fallback values
        return "Extraction Failed", html_content
