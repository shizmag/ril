import re
import datetime
import logging
import shutil
from pathlib import Path
from typing import Dict, Any, Optional

from ril import config
from ril.crawler import fetch_html
from ril.readability_utils import extract_article
from ril.converters import BaseConverter, MarkdownConverter, HTMLConverter
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

def delete_article(article_id: int) -> bool:
    """Delete an article from database and clean up its files (markdown and images)."""
    # 1. Get article metadata to find file paths
    article = db.get_article(article_id)
    if not article:
        logger.warning(f"Article with ID {article_id} not found in DB.")
        return False
        
    file_path = Path(article['file_path'])
    
    # 2. Delete from DB
    deleted = db.delete_article(article_id)
    if not deleted:
        return False
        
    # 3. Delete markdown file
    try:
        if file_path.exists():
            file_path.unlink()
            logger.info(f"Deleted file: {file_path}")
    except Exception as e:
        logger.error(f"Error deleting markdown file {file_path}: {e}")
        
    # 4. Delete images folder
    # The image directory name is the slug of the file, which is file_path.stem
    slug = file_path.stem
    img_dir = config.LIBRARY_DIR / "images" / slug
    try:
        if img_dir.exists() and img_dir.is_dir():
            shutil.rmtree(img_dir)
            logger.info(f"Deleted image folder: {img_dir}")
    except Exception as e:
        logger.error(f"Error deleting image folder {img_dir}: {e}")
        
    return True

def reset_library() -> None:
    """Clear all database tables and remove all markdown files/images in the library."""
    # 1. Clear database tables
    with db.get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM articles")
        try:
            cursor.execute("DELETE FROM articles_fts")
        except Exception as e:
            logger.warning(f"Error clearing articles_fts: {e}")
        conn.commit()
    
    # 2. Clean up files in library directory
    try:
        db_path_resolved = config.DB_PATH.resolve()
        for item in config.LIBRARY_DIR.iterdir():
            # Skip database file itself
            if item.resolve() == db_path_resolved:
                continue
            
            if item.is_file():
                try:
                    item.unlink()
                    logger.info(f"Deleted file during reset: {item}")
                except Exception as e:
                    logger.error(f"Error deleting file {item}: {e}")
            elif item.is_dir() and item.name == "images":
                # Clean all subfolders in images/
                for img_sub in item.iterdir():
                    try:
                        if img_sub.is_dir():
                            shutil.rmtree(img_sub)
                        else:
                            img_sub.unlink()
                    except Exception as e:
                        logger.error(f"Error cleaning image subdir {img_sub}: {e}")
            elif item.is_dir():
                try:
                    shutil.rmtree(item)
                    logger.info(f"Deleted directory during reset: {item}")
                except Exception as e:
                    logger.error(f"Error deleting directory {item}: {e}")
    except Exception as e:
        logger.error(f"Error resetting files: {e}")
