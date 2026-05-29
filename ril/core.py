import re
import datetime
import logging
from pathlib import Path
from typing import Dict, Any, Optional

from ril import config
from ril.crawler import fetch_html
from ril.readability_utils import extract_article
from ril.converters import BaseConverter, MarkdownConverter
from ril import db

logger = logging.getLogger(__name__)

# Initialize DB on load
db.init_db()

def sanitize_filename(title: str) -> str:
    """
    Sanitize the article title to create a clean, filesystem-safe filename.
    Supports Unicode (e.g. Cyrillic) and replaces spaces/special chars with underscores.
    """
    # Lowercase
    name = title.lower().strip()
    # Replace slashes, colons, and basic non-word chars except spaces and dashes
    # \w matches unicode characters (like Russian Cyrillic letters) in Python 3
    name = re.sub(r'[^\w\s-]', '', name)
    # Replace spaces and consecutive underscores with a single underscore
    name = re.sub(r'[\s_]+', '_', name)
    # Limit length to avoid path errors
    return name.strip('_')[:60]

async def process_url(
    url: str,
    converter: Optional[BaseConverter] = None
) -> Dict[str, Any]:
    """
    Run the full Read It Later pipeline for a URL.
    1. Fetch HTML using Playwright
    2. Extract core text using Readability
    3. Convert content using the chosen Converter adapter
    4. Save Markdown/images locally
    5. Save metadata and index for Search in SQLite
    """
    if not converter:
        converter = MarkdownConverter()
        
    logger.info(f"Processing URL: {url}")
    
    # 1. Fetch
    html = await fetch_html(url)
    
    # 2. Extract title & clean HTML
    title, clean_html = extract_article(html)
    
    # Generate unique slug with date prefix
    date_str = datetime.date.today().strftime("%Y-%m-%d")
    clean_title = sanitize_filename(title)
    slug = f"{date_str}_{clean_title}"
    if not clean_title:
        slug = f"{date_str}_article"
        
    # 3. Convert (downloads images and changes paths)
    content = await converter.convert(clean_html, url, slug)
    
    # 4. Save file
    file_name = f"{slug}{converter.file_extension}"
    file_path = config.LIBRARY_DIR / file_name
    
    with open(file_path, "w", encoding="utf-8") as f:
        f.write(content)
        
    # Calculate word and char count
    word_count = len(re.findall(r'\w+', content))
    char_count = len(content)
    
    # 5. Save to database & FTS
    article_id = db.add_article(
        url=url,
        title=title,
        file_path=str(file_path),
        word_count=word_count,
        char_count=char_count,
        content=content
    )
    
    logger.info(f"Successfully saved article: {title} (ID: {article_id})")
    
    return {
        "id": article_id,
        "url": url,
        "title": title,
        "file_path": str(file_path),
        "word_count": word_count,
        "char_count": char_count,
        "status": "unread"
    }
