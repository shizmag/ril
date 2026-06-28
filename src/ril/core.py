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
    # Limit length to avoid path errors, then strip leading/trailing underscores and hyphens
    return name[:60].strip('_-')

def download_pdf(url: str) -> Path:
    """Download PDF to a temporary file."""
    import urllib.request
    import tempfile
    import shutil
    
    headers = {
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36'
    }
    req = urllib.request.Request(url, headers=headers)
    
    temp_dir = Path(tempfile.gettempdir())
    temp_pdf_path = temp_dir / f"download_{tempfile.mktemp(dir='')}.pdf"
    
    logger.info(f"Downloading PDF from {url} to {temp_pdf_path}...")
    with urllib.request.urlopen(req) as response:
        with open(temp_pdf_path, 'wb') as out_file:
            shutil.copyfileobj(response, out_file)
            
    return temp_pdf_path

async def process_url(
    url: str,
    converter: Optional[BaseConverter] = None
) -> Dict[str, Any]:
    """
    Run the full Read It Later pipeline for a URL.
    Supports PDF parsing via marker-pdf.
    1. Fetch HTML using Playwright or download PDF directly
    2. Convert content (using marker-pdf for PDFs, or chosen converter for HTML)
    3. Save Markdown/images locally
    4. Save metadata and index for Search in SQLite
    """
    if not converter:
        converter = MarkdownConverter()
        
    logger.info(f"Processing URL: {url}")
    
    url_lower = url.lower()
    is_pdf = url_lower.split('?')[0].endswith('.pdf') or '/pdf/' in url_lower
    temp_pdf_path = None
    
    if is_pdf:
        try:
            temp_pdf_path = download_pdf(url)
        except Exception as e:
            logger.error(f"Failed to download PDF directly: {e}")
            raise e
    else:
        try:
            html = await fetch_html(url)
        except Exception as e:
            if "Download is starting" in str(e):
                is_pdf = True
                try:
                    temp_pdf_path = download_pdf(url)
                except Exception as e2:
                    logger.error(f"Failed to download PDF after Playwright download trigger: {e2}")
                    raise e2
            else:
                raise e

    if is_pdf:
        pdf_title = None
        pdf_markdown = ""
        try:
            from marker.config.parser import ConfigParser
            from marker.models import create_model_dict
            from marker.output import text_from_rendered
            import base64
            import io
            import os
            
            os.environ["PYTORCH_ENABLE_MPS_FALLBACK"] = "1"
            
            logger.info("Initializing marker-pdf models...")
            models = create_model_dict()
            config_parser = ConfigParser({"output_format": "markdown", "disable_ocr": True})

            converter_cls = config_parser.get_converter_cls()
            converter_obj = converter_cls(
                config=config_parser.generate_config_dict(),
                artifact_dict=models,
                processor_list=config_parser.get_processors(),
                renderer=config_parser.get_renderer(),
                llm_service=config_parser.get_llm_service(),
            )
            
            logger.info(f"Converting PDF {temp_pdf_path} using marker-pdf...")
            rendered = converter_obj(str(temp_pdf_path))
            pdf_markdown, ext, images = text_from_rendered(rendered)
            
            if rendered.metadata and isinstance(rendered.metadata, dict):
                pdf_title = rendered.metadata.get("title")
                
            if not pdf_title:
                url_path = url.split('?')[0].split('/')[-1]
                if url_path:
                    pdf_title = url_path.replace(".pdf", "").replace("_", " ").replace("-", " ").title()
                else:
                    pdf_title = "PDF Document"
                    
            if images:
                image_refs = {}
                for idx, (img_name, img_obj) in enumerate(images.items()):
                    ref_id = f"img_ref_{idx}"
                    buffered = io.BytesIO()
                    img_format = img_obj.format if img_obj.format else "JPEG"
                    if img_obj.mode != "RGB" and img_format in ("JPEG", "JPG"):
                        img_obj = img_obj.convert("RGB")
                    img_obj.save(buffered, format=img_format)
                    img_b64 = base64.b64encode(buffered.getvalue()).decode("utf-8")
                    mime_type = f"image/{img_format.lower()}"
                    if mime_type == "image/jpg":
                        mime_type = "image/jpeg"
                    b64_uri = f"data:{mime_type};base64,{img_b64}"
                    image_refs[ref_id] = b64_uri
                    
                    escaped_name = re.escape(img_name)
                    pdf_markdown = re.sub(
                        rf'!\[(.*?)\]\({escaped_name}\)',
                        rf'![\1][{ref_id}]',
                        pdf_markdown
                    )
                pdf_markdown += "\n\n"
                for ref_id, b64_uri in image_refs.items():
                    pdf_markdown += f"[{ref_id}]: {b64_uri}\n"
                    
        except Exception as e:
            logger.error(f"Error converting PDF via marker-pdf: {e}")
            raise e
        finally:
            try:
                if temp_pdf_path and temp_pdf_path.exists():
                    temp_pdf_path.unlink()
            except Exception as e:
                logger.warning(f"Failed to delete temp PDF file: {e}")
                
        date_str = datetime.date.today().strftime("%Y-%m-%d")
        clean_title = sanitize_filename(pdf_title)
        slug = f"{date_str}_{clean_title}"
        if not clean_title:
            slug = f"{date_str}_pdf_document"
            
        file_name = f"{slug}.md"
        file_path = config.LIBRARY_DIR / file_name
        
        config.LIBRARY_DIR.mkdir(parents=True, exist_ok=True)
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(pdf_markdown)
            
        search_text = re.sub(r'[*#_`\[\]\-!]', ' ', pdf_markdown)
        word_count = len(re.findall(r'\w+', search_text))
        char_count = len(search_text)
        
        article_id = db.add_article(
            url=url,
            title=pdf_title,
            file_path=str(file_path),
            word_count=word_count,
            char_count=char_count,
            content=search_text
        )
        
        logger.info(f"Successfully saved PDF article: {pdf_title} (ID: {article_id})")
        
        return {
            "id": article_id,
            "url": url,
            "title": pdf_title,
            "file_path": str(file_path),
            "word_count": word_count,
            "char_count": char_count,
            "status": "unread"
        }
    else:
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
        
        # Ensure library directory exists before writing to it
        config.LIBRARY_DIR.mkdir(parents=True, exist_ok=True)
        (config.LIBRARY_DIR / "images").mkdir(parents=True, exist_ok=True)
        
        if isinstance(content, bytes):
            with open(file_path, "wb") as f:
                f.write(content)
        else:
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(content)
            
        # Extract plain text for word counting and search indexing
        from bs4 import BeautifulSoup
        search_soup = BeautifulSoup(clean_html, "lxml")
        search_text = search_soup.get_text(separator=" ")
        
        # Calculate word and char count based on plain text
        word_count = len(re.findall(r'\w+', search_text))
        char_count = len(search_text)
        
        # 5. Save to database & FTS
        article_id = db.add_article(
            url=url,
            title=title,
            file_path=str(file_path),
            word_count=word_count,
            char_count=char_count,
            content=search_text
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
        if not config.LIBRARY_DIR.exists():
            return
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
