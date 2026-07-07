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
    """Download PDF to a temporary file using a secure NamedTemporaryFile."""
    import urllib.request
    import tempfile
    import shutil
    
    headers = {
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36'
    }
    req = urllib.request.Request(url, headers=headers)
    
    # Use mkstemp for a race-condition-free, secure temp file
    fd, tmp_name = tempfile.mkstemp(suffix=".pdf")
    temp_pdf_path = Path(tmp_name)
    
    logger.info(f"Downloading PDF from {url} to {temp_pdf_path}...")
    try:
        with urllib.request.urlopen(req) as response:
            with open(fd, 'wb', closefd=True) as out_file:
                shutil.copyfileobj(response, out_file)
    except Exception:
        # Close fd in case of error (already closed if open() succeeded)
        try:
            import os
            os.close(fd)
        except OSError:
            pass
        raise
            
    return temp_pdf_path


def convert_pdf_with_marker(pdf_path: Path, force_ocr: bool = False) -> tuple:
    """
    Convert a local PDF file to Markdown using marker-pdf.

    Returns:
        (markdown: str, title: str | None, images: dict)
        where images maps image filename -> PIL.Image object.

    This is a standalone function so it can be monkey-patched in tests
    without loading any ML models.
    """
    import os
    from marker.config.parser import ConfigParser
    from marker.models import create_model_dict
    from marker.output import text_from_rendered

    os.environ["PYTORCH_ENABLE_MPS_FALLBACK"] = "1"
    if force_ocr:
        os.environ["FORCE_OCR"] = "1"
        os.environ["EXTRACT_IMAGES"] = "True"

    logger.info("Initializing marker-pdf models...")
    models = create_model_dict()
    config_parser = ConfigParser({"output_format": "markdown", "disable_ocr": not force_ocr})

    converter_cls = config_parser.get_converter_cls()
    converter_obj = converter_cls(
        config=config_parser.generate_config_dict(),
        artifact_dict=models,
        processor_list=config_parser.get_processors(),
        renderer=config_parser.get_renderer(),
        llm_service=config_parser.get_llm_service(),
    )

    logger.info(f"Converting PDF {pdf_path} using marker-pdf...")
    rendered = converter_obj(str(pdf_path))
    pdf_markdown, _ext, images = text_from_rendered(rendered)

    pdf_title: Optional[str] = None
    if rendered.metadata and isinstance(rendered.metadata, dict):
        pdf_title = rendered.metadata.get("title") or None

    return pdf_markdown, pdf_title, images or {}

async def process_url(
    url: str,
    converter: Optional[BaseConverter] = None,
    force: bool = False,
    rasterize_svg: bool = False,
    force_ocr: bool = False
) -> Dict[str, Any]:
    """
    Run the full Read It Later pipeline for a URL.
    Supports PDF parsing via marker-pdf.
    1. Fetch HTML using Playwright or download PDF directly
    2. Convert content (using marker-pdf for PDFs, or chosen converter for HTML)
    3. Save Markdown/images locally
    4. Save metadata and index for Search in SQLite
    """
    if not force:
        existing = db.get_article_by_url(url)
        if existing:
            raise ValueError(f"URL already exists in library (ID: {existing['id']})")

    if not converter:
        from ril.converters import EPUBConverter
        converter = EPUBConverter()
        
    logger.info(f"Processing URL: {url} (rasterize_svg={rasterize_svg}, force_ocr={force_ocr})")
    
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
            html = await fetch_html(url, rasterize_svg=rasterize_svg)
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
        images: dict = {}
        try:
            import base64
            import io

            pdf_markdown, pdf_title, images = convert_pdf_with_marker(temp_pdf_path, force_ocr=force_ocr)

            if not pdf_title:
                url_path = url.split('?')[0].split('/')[-1]
                if url_path:
                    pdf_title = url_path.replace(".pdf", "").replace("_", " ").replace("-", " ").title()
                else:
                    pdf_title = "PDF Document"

            if images and not config.DISABLE_IMAGES:
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
            else:
                # Strip all markdown image tags
                pdf_markdown = re.sub(r'!\[.*?\]\(.*?\)', '', pdf_markdown)
                pdf_markdown = re.sub(r'!\[.*?\]\[.*?\]', '', pdf_markdown)

        except Exception as e:
            logger.error(f"Error converting PDF via marker-pdf: {e}")
            raise e
        finally:
            try:
                if temp_pdf_path and temp_pdf_path.exists():
                    temp_pdf_path.unlink()
            except Exception as cleanup_err:
                logger.warning(f"Failed to delete temp PDF file: {cleanup_err}")
                
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
            
        # Clean pdf_markdown from images for search indexing and word counting
        clean_pdf_markdown = pdf_markdown
        clean_pdf_markdown = re.sub(r'(?m)^\[img_ref_\w+\].*$', '', clean_pdf_markdown)
        clean_pdf_markdown = re.sub(r'!\[.*?\]\[img_ref_\w+\]', '', clean_pdf_markdown)
        clean_pdf_markdown = re.sub(r'!\[.*?\]\[.*?\]', '', clean_pdf_markdown)
        clean_pdf_markdown = re.sub(r'!\[.*?\]\(.*?\)', '', clean_pdf_markdown)
        clean_pdf_markdown = re.sub(r'data:image/[^;]+;base64,[A-Za-z0-9+/=\s\r\n]+', '', clean_pdf_markdown)
        
        search_text = re.sub(r'[*#_`\[\]\-!]', ' ', clean_pdf_markdown)
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
                
        # Save clean HTML source for future conversion/export
        try:
            clean_html_path = file_path.with_suffix('.html_clean')
            with open(clean_html_path, "w", encoding="utf-8") as f:
                f.write(clean_html)
        except Exception as e:
            logger.error(f"Failed to save clean html: {e}")
            
        # Extract plain text for word counting and search indexing
        from bs4 import BeautifulSoup
        search_soup = BeautifulSoup(clean_html, "lxml")
        for tag in search_soup.find_all(["script", "style", "svg", "img"]):
            tag.decompose()
        search_text = search_soup.get_text(separator=" ")
        
        # Strip all raw base64 data URIs
        search_text = re.sub(r'data:image/[^;]+;base64,[A-Za-z0-9+/=\s\r\n]+', '', search_text)
        
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

def md_to_html_fallback(md_content: str, title: str) -> str:
    lines = md_content.split('\n')
    html_lines = []
    in_code = False
    in_list = False
    for line in lines:
        line_strip = line.strip()
        if line_strip.startswith('```'):
            if in_code:
                html_lines.append('</pre>')
                in_code = False
            else:
                html_lines.append('<pre><code>')
                in_code = True
            continue
            
        if in_code:
            html_lines.append(line)
            continue
            
        if line_strip.startswith('#'):
            level = len(line) - len(line.lstrip('#'))
            header_text = line.lstrip('#').strip()
            html_lines.append(f'<h{level}>{header_text}</h{level}>')
            continue
            
        if line_strip.startswith('* ') or line_strip.startswith('- '):
            if not in_list:
                html_lines.append('<ul>')
                in_list = True
            html_lines.append(f'<li>{line_strip[2:]}</li>')
            continue
        else:
            if in_list:
                html_lines.append('</ul>')
                in_list = False
                
        if line_strip == '':
            html_lines.append('<br/>')
        else:
            html_lines.append(f'<p>{line_strip}</p>')
            
    if in_list:
        html_lines.append('</ul>')
    if in_code:
        html_lines.append('</pre>')
        
    body = '\n'.join(html_lines)
    import html as _html
    safe_title = _html.escape(title)
    return f"<html><head><title>{safe_title}</title></head><body>{body}</body></html>"

async def export_article(article_id: int, export_format: str) -> Dict[str, Any]:
    article = db.get_article(article_id)
    if not article:
        raise ValueError(f"Article with ID {article_id} not found")

    original_file_path = Path(article["file_path"])
    target_ext = f".{export_format.lower()}"
    target_file_path = original_file_path.with_suffix(target_ext)
    
    # If the file already exists, we reuse it (cache)
    if target_file_path.exists():
        return {
            "article_id": article_id,
            "title": article["title"],
            "format": export_format,
            "file_path": str(target_file_path),
            "filename": target_file_path.name,
            "word_count": article["word_count"],
            "status": article["status"],
            "rating": article["rating"],
            "tags": article["tags"]
        }
        
    # If not exists, generate it
    clean_html_path = original_file_path.with_suffix('.html_clean')
    
    html_content = ""
    base_url = article["url"]
    article_slug = original_file_path.stem
    
    if clean_html_path.exists():
        with open(clean_html_path, "r", encoding="utf-8") as f:
            html_content = f.read()
    else:
        # Fallback for old files or PDFs
        if original_file_path.suffix == ".md":
            md_content = ""
            if original_file_path.exists():
                with open(original_file_path, "r", encoding="utf-8", errors="ignore") as f:
                    md_content = f.read()
            html_content = md_to_html_fallback(md_content, article["title"])
        elif original_file_path.suffix == ".html":
            if original_file_path.exists():
                with open(original_file_path, "r", encoding="utf-8", errors="ignore") as f:
                    html_content = f.read()
        else:
            # Re-fetch as a last resort
            url_lower = base_url.lower()
            is_pdf = url_lower.split('?')[0].endswith('.pdf') or '/pdf/' in url_lower
            if not is_pdf:
                html = await fetch_html(base_url)
                _, html_content = extract_article(html)
                try:
                    with open(clean_html_path, "w", encoding="utf-8") as f:
                        f.write(html_content)
                except Exception as e:
                    logger.error(f"Failed to save clean html: {e}")
            else:
                raise ValueError("Cannot convert PDF without original file or markdown source")

    # Convert HTML content to target format
    fmt = export_format.lower()
    if fmt == "epub":
        from ril.converters import EPUBConverter
        converter = EPUBConverter()
        content = await converter.convert(html_content, base_url, article_slug)
        with open(target_file_path, "wb") as f:
            f.write(content)
    elif fmt == "html":
        from ril.converters import HTMLConverter
        converter = HTMLConverter()
        content = await converter.convert(html_content, base_url, article_slug)
        with open(target_file_path, "w", encoding="utf-8") as f:
            f.write(content)
    elif fmt == "markdown":
        from ril.converters import MarkdownConverter
        converter = MarkdownConverter()
        content = await converter.convert(html_content, base_url, article_slug)
        with open(target_file_path, "w", encoding="utf-8") as f:
            f.write(content)
    else:
        raise ValueError(f"Unsupported export format: {export_format}")
        
    return {
        "article_id": article_id,
        "title": article["title"],
        "format": export_format,
        "file_path": str(target_file_path),
        "filename": target_file_path.name,
        "word_count": article["word_count"],
        "status": article["status"],
        "rating": article["rating"],
        "tags": article["tags"]
    }
