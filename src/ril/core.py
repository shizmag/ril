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

EXPORT_PIPELINE_VERSION = "2026-07-epub-fidelity-1"


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


def is_pdf_file(file_path: Path) -> bool:
    """Check if the file starts with the %PDF magic bytes."""
    try:
        if not file_path.exists():
            return False
        with open(file_path, "rb") as f:
            header = f.read(4)
            return header == b"%PDF"
    except Exception:
        return False


async def detect_pdf_url_or_content(url: str) -> bool:
    """
    Detect if the URL points to a PDF via heuristics or HEAD request.
    Gracefully handles HEAD errors (403, 405, timeouts, etc.) with fallbacks.
    """
    url_lower = url.lower()
    is_pdf = url_lower.split('?')[0].endswith('.pdf') or '/pdf/' in url_lower
    
    if not url.startswith(("http://", "https://")):
        return is_pdf
        
    try:
        import httpx
        async with httpx.AsyncClient(follow_redirects=True, timeout=2.0) as client:
            resp = await client.head(url)
            if resp.status_code == 200:
                content_type = resp.headers.get("content-type", "").lower()
                if "application/pdf" in content_type:
                    return True
                elif "text/html" in content_type:
                    return is_pdf
            else:
                logger.debug(f"HEAD request returned status code {resp.status_code} for {url}")
    except Exception as e:
        logger.debug(f"HEAD request failed for {url}: {e}")
        
    return is_pdf



def convert_pdf_with_marker(pdf_path: Path, force_ocr: bool = False) -> tuple:
    """
    Convert a local PDF file to Markdown using marker-pdf.

    Returns:
        (markdown: str, title: str | None, images: dict, marker_meta: dict)
        where images maps image filename -> PIL.Image object and marker_meta
        holds PDF document metadata fields from marker (title, author, subject).

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
    marker_meta: Dict[str, Any] = {}
    if rendered.metadata and isinstance(rendered.metadata, dict):
        pdf_title = rendered.metadata.get("title") or None
        marker_meta = {
            "marker_title": rendered.metadata.get("title"),
            "marker_author": rendered.metadata.get("author"),
            "marker_subject": rendered.metadata.get("subject"),
        }

    return pdf_markdown, pdf_title, images or {}, marker_meta

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
    
    is_pdf = await detect_pdf_url_or_content(url)

    temp_pdf_path = None
    readability_title = None
    clean_html = None
    html = None
    
    if is_pdf:
        try:
            temp_pdf_path = download_pdf(url)
            if not is_pdf_file(temp_pdf_path):
                logger.warning(f"Downloaded content from {url} is not a valid PDF (missing %PDF header)")
                try:
                    with open(temp_pdf_path, "r", encoding="utf-8", errors="ignore") as f:
                        content_sample = f.read(1024).strip().lower()
                    if "<html" in content_sample or "<!doctype html" in content_sample:
                        logger.info("Downloaded content is HTML. Switching to HTML pipeline.")
                        with open(temp_pdf_path, "r", encoding="utf-8", errors="ignore") as f:
                            html = f.read()
                        is_pdf = False
                        temp_pdf_path.unlink()
                        temp_pdf_path = None
                    else:
                        raise ValueError("Downloaded file is neither PDF nor HTML")
                except Exception as fe:
                    if temp_pdf_path and temp_pdf_path.exists():
                        temp_pdf_path.unlink()
                    raise ValueError(f"Invalid PDF or HTML downloaded from {url}: {fe}")
        except Exception as e:
            if not is_pdf:
                pass
            else:
                logger.error(f"Failed to download PDF directly: {e}")
                raise e

    if not is_pdf:
        if html is None:
            try:
                html = await fetch_html(url, rasterize_svg=rasterize_svg)
            except Exception as e:
                if "Download is starting" in str(e) or "download" in str(e).lower():
                    is_pdf = True
                    try:
                        temp_pdf_path = download_pdf(url)
                        if not is_pdf_file(temp_pdf_path):
                            if temp_pdf_path.exists():
                                temp_pdf_path.unlink()
                            raise ValueError("Downloaded file after Playwright download trigger is not a valid PDF")
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

            pdf_markdown, pdf_title, images, marker_meta = convert_pdf_with_marker(
                temp_pdf_path, force_ocr=force_ocr
            )

            if not pdf_title:
                if readability_title:
                    pdf_title = readability_title
                else:
                    url_path = url.split('?')[0].split('/')[-1]
                    if url_path:
                        pdf_title = url_path.replace(".pdf", "").replace("_", " ").replace("-", " ").title()
                    else:
                        pdf_title = "PDF Document"

            if images and not config.DISABLE_IMAGES:
                image_refs = {}
                matched_image_keys = set()
                for idx, (img_name, img_obj) in enumerate(images.items()):
                    ref_id = f"img_ref_{idx}"
                    buffered = io.BytesIO()
                    img_format = img_obj.format if img_obj.format else "JPEG"
                    if img_obj.mode != "RGB" and img_format in ("JPEG", "JPG"):
                        img_obj = img_obj.convert("RGB")
                    try:
                        from PIL import ImageFile
                        ImageFile.LOAD_TRUNCATED_IMAGES = True
                        img_obj.load()
                        img_obj.save(buffered, format=img_format)
                    except Exception as save_err:
                        logger.warning(f"Failed to save image {img_name} as {img_format}: {save_err}. Falling back to PNG.")
                        buffered = io.BytesIO()
                        img_format = "PNG"
                        try:
                            if img_obj.mode not in ("RGB", "RGBA"):
                                img_obj = img_obj.convert("RGBA" if "transparency" in img_obj.info or img_obj.mode == "P" else "RGB")
                            img_obj.save(buffered, format="PNG")
                        except Exception as png_err:
                            logger.error(f"Failed to save image {img_name} as PNG fallback: {png_err}")
                            continue

                    img_b64 = base64.b64encode(buffered.getvalue()).decode("utf-8")
                    mime_type = f"image/{img_format.lower()}"
                    if mime_type == "image/jpg":
                        mime_type = "image/jpeg"
                    b64_uri = f"data:{mime_type};base64,{img_b64}"
                    image_refs[ref_id] = b64_uri

                    candidate_names = [img_name]
                    basename = Path(img_name).name
                    if basename != img_name:
                        candidate_names.append(basename)

                    for candidate in candidate_names:
                        escaped_name = re.escape(candidate)
                        if candidate == img_name:
                            pattern = rf'!\[(.*?)\]\({escaped_name}\)'
                        else:
                            # Basename fallback: markdown may use a subdir prefix
                            pattern = (
                                rf'!\[(.*?)\]\((?:{escaped_name}|[^)]*/{escaped_name})\)'
                            )
                        pdf_markdown, replacements = re.subn(
                            pattern,
                            rf'![\1][{ref_id}]',
                            pdf_markdown,
                        )
                        if replacements:
                            matched_image_keys.add(img_name)
                            break

                for unmatched_key in set(images.keys()) - matched_image_keys:
                    logger.warning(f"PDF image key not matched in markdown: {unmatched_key}")

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
        (config.LIBRARY_DIR / "images").mkdir(parents=True, exist_ok=True)
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(pdf_markdown)

        if config.CACHE_PDF_HTML:
            try:
                from ril.converters import validate_and_normalize_math

                clean_html_path = file_path.with_suffix(".html_clean")
                normalized_md = validate_and_normalize_math(pdf_markdown)
                cached_html = md_to_html_fallback(normalized_md, pdf_title)
                with open(clean_html_path, "w", encoding="utf-8") as f:
                    f.write(cached_html)
            except Exception as e:
                logger.error(f"Failed to save PDF html cache: {e}")

        # Save import sidecar metadata from marker-pdf
        import json
        sidecar_path = file_path.with_suffix(".meta.json")
        sidecar_data = {
            "marker_title": marker_meta.get("marker_title"),
            "marker_author": marker_meta.get("marker_author"),
            "marker_subject": marker_meta.get("marker_subject"),
            "imported_at": datetime.datetime.now().isoformat(),
        }
        try:
            with open(sidecar_path, "w", encoding="utf-8") as mf:
                json.dump(sidecar_data, mf, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"Failed to save PDF sidecar meta {sidecar_path}: {e}")
            
        # Save clean HTML source for future conversion/export if it came from web page
        if clean_html:
            try:
                clean_html_path = file_path.with_suffix('.html_clean')
                with open(clean_html_path, "w", encoding="utf-8") as f:
                    f.write(clean_html)
            except Exception as e:
                logger.error(f"Failed to save clean html: {e}")

        # Clean pdf_markdown from images for search indexing and word counting
        clean_pdf_markdown = pdf_markdown
        clean_pdf_markdown = re.sub(r'data:image/[^;]+;base64,[A-Za-z0-9+/=\s\r\n]+', '', clean_pdf_markdown)
        clean_pdf_markdown = re.sub(r'(?m)^\[img_ref_\w+\].*$', '', clean_pdf_markdown)
        clean_pdf_markdown = re.sub(r'!\[.*?\]\[img_ref_\w+\]', '', clean_pdf_markdown)
        clean_pdf_markdown = re.sub(r'!\[.*?\]\[.*?\]', '', clean_pdf_markdown)
        clean_pdf_markdown = re.sub(r'!\[.*?\]\(.*?\)', '', clean_pdf_markdown)
        
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
        
    # 3. Delete files (main file, exports, and meta files)
    try:
        if file_path.exists():
            file_path.unlink()
            logger.info(f"Deleted file: {file_path}")
            
        for ext in [".epub", ".html", ".md", ".meta.json", ".epub.meta.json", ".html.meta.json", ".md.meta.json", ".html_clean"]:
            sibling = file_path.with_suffix(ext)
            if sibling.exists():
                sibling.unlink()
                logger.info(f"Deleted export/meta file: {sibling}")
    except Exception as e:
        logger.error(f"Error deleting files for article {file_path}: {e}")
        
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

_FIGURE_CAPTION_RE = re.compile(r"^Figure\s+\d+\s*:", re.IGNORECASE)
_FIGURE_BLOCK_HTML_RE = re.compile(
    r"<p>\s*(<img\b[^>]*>)\s*</p>\s*<p>\s*<em>(Figure\s+\d+\s*:[^<]*)</em>\s*</p>",
    re.IGNORECASE,
)


def wrap_figures_with_captions(soup) -> None:
    """
    Wrap block images followed by italic *Figure N:* captions into semantic figure/figcaption.
    Inline images inside paragraphs with surrounding text are left unchanged.
    """
    from bs4 import NavigableString

    root = soup.body if soup.body else soup

    def _paragraph_image_only(paragraph):
        imgs = paragraph.find_all("img", recursive=False)
        if len(imgs) != 1:
            return None
        img = imgs[0]
        for child in paragraph.children:
            if child is img:
                continue
            if isinstance(child, NavigableString):
                if child.strip():
                    return None
            else:
                return None
        return img

    def _figure_caption_text(paragraph):
        em_tags = paragraph.find_all("em", recursive=False)
        if len(em_tags) != 1:
            return None
        em = em_tags[0]
        for child in paragraph.children:
            if child is em:
                continue
            if isinstance(child, NavigableString):
                if child.strip():
                    return None
            else:
                return None
        caption = em.get_text(strip=True)
        if _FIGURE_CAPTION_RE.match(caption):
            return caption
        return None

    for paragraph in list(root.find_all("p")):
        if paragraph.parent and paragraph.parent.name == "figure":
            continue

        img = _paragraph_image_only(paragraph)
        if img is None:
            continue

        next_sibling = paragraph.find_next_sibling()
        if not next_sibling or next_sibling.name != "p":
            continue

        caption = _figure_caption_text(next_sibling)
        if not caption:
            continue

        figure = soup.new_tag("figure")
        img.extract()
        figure.append(img)

        figcaption = soup.new_tag("figcaption")
        figcaption.string = caption
        figure.append(figcaption)

        paragraph.replace_with(figure)
        next_sibling.decompose()


def _wrap_figures_in_html(html_out: str) -> str:
    """Regex-based figure wrapping to avoid re-serializing unrelated HTML nodes."""

    def _repl(match: re.Match) -> str:
        img_tag = match.group(1)
        caption = match.group(2)
        return f"<figure>{img_tag}<figcaption>{caption}</figcaption></figure>"

    return _FIGURE_BLOCK_HTML_RE.sub(_repl, html_out)


def md_to_html_fallback(md_content: str, title: str) -> str:
    """
    Fallback markdown to HTML parser using python-markdown.
    """
    import re
    import markdown
    import html as _html

    if not md_content:
        md_content = ""

    placeholders = {}
    placeholder_count = 0

    def _math_block_wrapper(latex: str) -> str:
        safe_latex = _html.escape(latex.strip(), quote=True)
        return f'<div class="math-block" data-latex="{safe_latex}"></div>'

    def _math_inline_wrapper(latex: str) -> str:
        safe_latex = _html.escape(latex.strip(), quote=True)
        return f'<span class="math-inline" data-latex="{safe_latex}"></span>'

    def repl_block_bracket(match):
        nonlocal placeholder_count
        ph = f"MATHBLOCKPLACEHOLDER{placeholder_count}"
        placeholder_count += 1
        placeholders[ph] = _math_block_wrapper(match.group(1))
        return ph

    def repl_block_double_dollar(match):
        nonlocal placeholder_count
        ph = f"MATHBLOCKPLACEHOLDER{placeholder_count}"
        placeholder_count += 1
        placeholders[ph] = _math_block_wrapper(match.group(1))
        return ph

    def repl_inline_paren(match):
        nonlocal placeholder_count
        ph = f"MATHINLINEPLACEHOLDER{placeholder_count}"
        placeholder_count += 1
        placeholders[ph] = _math_inline_wrapper(match.group(1))
        return ph

    def repl_inline_dollar(match):
        nonlocal placeholder_count
        ph = f"MATHINLINEPLACEHOLDER{placeholder_count}"
        placeholder_count += 1
        placeholders[ph] = _math_inline_wrapper(match.group(1))
        return ph

    # Code protection logic
    code_placeholders = {}
    code_count = 0

    def repl_code(match):
        nonlocal code_count
        ph = f"CODEPLACEHOLDER{code_count}"
        code_count += 1
        code_placeholders[ph] = match.group(0)
        return ph

    content = md_content

    # 1. Hide code blocks (HTML pre, HTML code, fenced code blocks, inline backticks)
    content = re.sub(r'<pre[\s\S]*?</pre>', repl_code, content, flags=re.IGNORECASE)
    content = re.sub(r'<code[\s\S]*?</code>', repl_code, content, flags=re.IGNORECASE)
    content = re.sub(r'```[\s\S]*?```', repl_code, content)
    content = re.sub(r'`[^`\n]+`', repl_code, content)

    # 2. Extract math delimiters to avoid markdown formatting issues inside equations
    content = re.sub(r'\\\[(.*?)\\\]', repl_block_bracket, content, flags=re.DOTALL)
    content = re.sub(r'\$\$(.*?)\$\$', repl_block_double_dollar, content, flags=re.DOTALL)
    content = re.sub(r'\\\((.*?)\\\)', repl_inline_paren, content, flags=re.DOTALL)
    content = re.sub(r'\$([^\$\s](?:[^\$]*?[^\$\s])?)\$', repl_inline_dollar, content)

    # 3. Restore code blocks before running markdown parsing
    for ph, orig in code_placeholders.items():
        content = content.replace(ph, orig)

    html_out = markdown.markdown(
        content,
        extensions=[
            "extra",
            "sane_lists",
            "toc",
        ]
    )

    for ph, orig in placeholders.items():
        html_out = html_out.replace(ph, orig)

    html_out = _wrap_figures_in_html(html_out)

    safe_title = _html.escape(title)
    return f"<html><head><title>{safe_title}</title></head><body>{html_out}</body></html>"


def _format_article_date(added_at: Optional[str]) -> str:
    """Extract YYYY-MM-DD from article added_at ISO timestamp."""
    if added_at and len(added_at) >= 10:
        return added_at[:10]
    return datetime.date.today().strftime("%Y-%m-%d")


def build_epub_metadata(
    article: Dict[str, Any],
    html_content: str,
    original_file_path: Path,
) -> Dict[str, Any]:
    """Build EPUB dc metadata from article DB row and optional import sidecar."""
    import json
    from ril.converters import detect_language

    metadata: Dict[str, Any] = {
        "title": article.get("title") or "Saved Article",
        "date": _format_article_date(article.get("added_at")),
        "creator": "Read It Later",
    }

    sidecar_path = original_file_path.with_suffix(".meta.json")
    if sidecar_path.exists():
        try:
            with open(sidecar_path, "r", encoding="utf-8") as f:
                sidecar = json.load(f)
            if sidecar.get("marker_author"):
                metadata["creator"] = sidecar["marker_author"]
        except Exception as e:
            logger.debug(f"Failed to read sidecar meta {sidecar_path}: {e}")

    sample_text = f"{metadata.get('title', '')} {html_content[:5000]}"
    metadata["language"] = detect_language(sample_text)
    return metadata


async def export_article(article_id: int, export_format: str, force: bool = False) -> Dict[str, Any]:
    article = db.get_article(article_id)
    if not article:
        raise ValueError(f"Article with ID {article_id} not found")

    original_file_path = Path(article["file_path"])
    target_ext = f".{export_format.lower()}"
    target_file_path = original_file_path.with_suffix(target_ext)
    meta_file_path = target_file_path.with_suffix(target_file_path.suffix + ".meta.json")
    
    import json
    # Check if target file and meta file both exist and version matches
    cache_valid = False
    if target_file_path.exists() and meta_file_path.exists():
        try:
            with open(meta_file_path, "r", encoding="utf-8") as mf:
                meta_data = json.load(mf)
                if meta_data.get("export_pipeline_version") == EXPORT_PIPELINE_VERSION:
                    cache_valid = True
        except Exception as e:
            logger.debug(f"Failed to read/parse meta file {meta_file_path}: {e}")

    # If the file already exists and cache is valid, we reuse it (cache)
    if not force and cache_valid:
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
        
    # If not valid/forced, generate it
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
            from ril.converters import validate_and_normalize_math

            md_content = ""
            if original_file_path.exists():
                with open(original_file_path, "r", encoding="utf-8", errors="ignore") as f:
                    md_content = f.read()
            md_content = validate_and_normalize_math(md_content)
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
        epub_metadata = build_epub_metadata(article, html_content, original_file_path)
        content = await converter.convert(
            html_content, base_url, article_slug, metadata=epub_metadata
        )
        with open(target_file_path, "wb") as f:
            f.write(content)
        if config.RIL_EPUB_DEBUG:
            from ril.converters import build_epub_debug_report

            report_path = target_file_path.with_suffix(".epub.report.json")
            try:
                with open(report_path, "w", encoding="utf-8") as rf:
                    json.dump(build_epub_debug_report(content), rf, indent=2)
            except Exception as e:
                logger.error(f"Failed to save EPUB debug report {report_path}: {e}")
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
        
    # Write meta file
    try:
        with open(meta_file_path, "w", encoding="utf-8") as mf:
            json.dump({"export_pipeline_version": EXPORT_PIPELINE_VERSION}, mf)
    except Exception as e:
        logger.error(f"Failed to save meta file {meta_file_path}: {e}")
        
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
