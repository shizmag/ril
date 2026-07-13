import os
import re
import io
import datetime
import html
import base64
import hashlib
import logging
import asyncio
import zipfile
import uuid
import mimetypes
import xml.etree.ElementTree as ET
from abc import ABC, abstractmethod
from typing import Optional, Tuple, Dict, Any, List
from urllib.parse import urljoin, urlparse, parse_qs, urlunparse, urlencode, unquote, quote
from pathlib import Path
import httpx
from bs4 import BeautifulSoup
import markdownify
import latex2mathml.converter
from mathml_to_latex import MathMLToLaTeX

from ril import config

logger = logging.getLogger(__name__)

class BaseConverter(ABC):
    """
    Abstract Base class for converting HTML to other formats (Adapter Pattern).
    """
    @abstractmethod
    async def convert(self, html_content: str, base_url: str, article_slug: str) -> str:
        """
        Convert HTML content and return the final string output.
        """
        pass

    @property
    @abstractmethod
    def file_extension(self) -> str:
        """
        Return the file extension for this format (e.g. '.md').
        """
        pass

    async def _download_single_image_bytes(
        self,
        client: httpx.AsyncClient,
        url: str,
        semaphore: asyncio.Semaphore,
        referer: Optional[str] = None,
        role: Optional[str] = None,
        preserve_text_quality: bool = False
    ) -> Optional[Tuple[bytes, str]]:
        """
        Download a single image and return its bytes and content-type.
        """
        async with semaphore:
            try:
                # Unquote first to handle already percent-encoded or double-encoded URLs, then encode safely
                url = unquote(url)
                if '%' in url:
                    url = unquote(url)
                url = quote(url, safe=':/?#[]@!$&\'()*+,;=')
                
                headers = {
                    "User-Agent": (
                        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/122.0.0.0 Safari/537.36"
                    ),
                    "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
                    "Accept-Language": "en-US,en;q=0.9,ru;q=0.8",
                }
                if referer:
                    # Unquote first to prevent double encoding
                    headers["Referer"] = quote(referer, safe=':/?#[]@!$&\'()*+,;=')

                retries = 3
                backoff = 0.5
                response = None

                for attempt in range(retries):
                    try:
                        response = await client.get(url, headers=headers)
                        if response.status_code == 429:
                            retry_after = response.headers.get("Retry-After")
                            try:
                                delay = float(retry_after) if retry_after else backoff
                            except ValueError:
                                delay = backoff
                            logger.warning(f"Rate limited (429) for image {url}. Retrying in {delay}s...")
                            await asyncio.sleep(delay)
                            backoff *= 2
                            continue
                        response.raise_for_status()
                        break
                    except Exception as e:
                        if isinstance(e, asyncio.CancelledError):
                            raise e
                        if attempt == retries - 1:
                            raise e
                        logger.warning(f"Error downloading image {url} (attempt {attempt + 1}/{retries}): {e}. Retrying in {backoff}s...")
                        await asyncio.sleep(backoff)
                        backoff *= 2

                if not response:
                    raise Exception("No response received from image server")

                response.raise_for_status()
                content_type = response.headers.get("content-type", "image/jpeg")
                
                # Check if it is SVG
                if "svg" in content_type.lower():
                    return response.content, content_type
                
                # Optimize image bytes using Pillow
                res = self._optimize_image(
                    response.content,
                    content_type,
                    role=role,
                    preserve_text_quality=preserve_text_quality
                )
                if not res:
                    return None
                return res
            except Exception as e:
                logger.error(f"Error downloading image bytes {url}: {e}")
                return None

    def _optimize_image(
        self,
        img_bytes: bytes,
        content_type: str,
        max_dim: int = 1200,
        quality: int = 78,
        role: Optional[str] = None,
        preserve_text_quality: bool = False
    ) -> Optional[Tuple[bytes, str]]:
        """
        Optimize downloaded image bytes using Pillow:
        1. Resizes to max_dim (1200px) keeping aspect ratio.
        2. Compresses JPEGs and other formats with quality=75-80%.
        3. Keeps transparency channel by converting to WebP.
        4. Filters out tiny spacer/tracker images (size <= 16x16).
        """
        try:
            from PIL import Image
            
            img = Image.open(io.BytesIO(img_bytes))
            
            # Filter out tiny spacer/tracker images (e.g. 16x16 or smaller)
            w, h = img.size
            if w <= 16 and h <= 16:
                logger.info(f"Skipping tiny/tracker image with dimensions {w}x{h}")
                return None
                
            is_high_quality_role = (
                role in {"formula", "math", "chart", "diagram", "table", "screenshot"}
                or preserve_text_quality
            )
            
            # For formula/math/chart/diagram/table, don't downscale below 1000px
            if is_high_quality_role:
                max_dim = max(max_dim, 1000)
                
            # 1. Resize if needed
            if w > max_dim or h > max_dim:
                if w > h:
                    new_w = max_dim
                    new_h = int(h * (max_dim / w))
                else:
                    new_h = max_dim
                    new_w = int(w * (max_dim / h))
                img = img.resize((new_w, new_h), Image.Resampling.LANCZOS)
                
            # 2. Determine format and save
            out_io = io.BytesIO()
            
            has_transparency = False
            if img.mode in ("RGBA", "LA"):
                alpha = img.split()[-1]
                # If minimum alpha is 255, the image is fully opaque
                has_transparency = alpha.getextrema()[0] < 255
            elif img.mode == "P" and "transparency" in img.info:
                has_transparency = True
                
            if is_high_quality_role:
                # Do not convert to JPEG for diagrams/charts/formulas
                # Keep original format if it was png/webp/gif, or default to PNG.
                if "webp" in content_type.lower() or has_transparency:
                    img.save(out_io, format="WEBP", quality=90)
                    mime_type = "image/webp"
                else:
                    img.save(out_io, format="PNG")
                    mime_type = "image/png"
            else:
                if has_transparency:
                    # Save as WEBP to keep transparency with high compression
                    img.save(out_io, format="WEBP", quality=quality)
                    mime_type = "image/webp"
                else:
                    # Save as progressive JPEG with quality compression
                    if img.mode != "RGB":
                        img = img.convert("RGB")
                    img.save(out_io, format="JPEG", quality=quality, optimize=True, progressive=True)
                    mime_type = "image/jpeg"
                
            optimized_bytes = out_io.getvalue()
            
            # Return optimized bytes only if they are smaller
            if len(optimized_bytes) < len(img_bytes):
                return optimized_bytes, mime_type
                
            # Otherwise return original
            fallback_mime = content_type
            if "jpg" in fallback_mime.lower():
                fallback_mime = "image/jpeg"
            return img_bytes, fallback_mime
            
        except Exception as e:
            logger.warning(f"Failed to optimize image: {e}. Using original bytes.")
            return img_bytes, content_type

    def _get_extension_from_mime(self, content_type: str) -> str:
        """Map mime type to common file extensions."""
        content_type = content_type.lower()
        if "image/png" in content_type:
            return ".png"
        elif "image/jpeg" in content_type or "image/jpg" in content_type:
            return ".jpg"
        elif "image/gif" in content_type:
            return ".gif"
        elif "image/webp" in content_type:
            return ".webp"
        elif "image/svg+xml" in content_type:
            return ".svg"
        return ""


class CustomMarkdownConverter(markdownify.MarkdownConverter):
    """
    Custom HTML-to-Markdown converter subclass of markdownify.
    Ensures blockquotes, paragraphs, headings, lists, code, figures, captions,
    iframes, and text styling are beautifully and cleanly formatted.
    """
    def convert_blockquote(self, el, text, parent_tags):
        if not text or not text.strip():
            return ''
        
        # Clean up leading/trailing newlines/spaces inside the blockquote
        clean_text = text.strip()
        
        # Collapse multiple empty lines to a single empty line
        clean_text = re.sub(r'\n{3,}', '\n\n', clean_text)
        
        lines = []
        for line in clean_text.split('\n'):
            stripped_line = line.strip()
            # Clean up existing blockquote prefix if present
            if stripped_line.startswith('>'):
                stripped_line = stripped_line[1:].strip()
            if stripped_line:
                lines.append(f"> {stripped_line}")
            else:
                lines.append(">")
        
        return '\n' + '\n'.join(lines) + '\n'

    def convert_p(self, el, text, parent_tags):
        if not text or not text.strip():
            return ''
            
        clean_text = text.replace('\r\n', '\n')
        # Replace newlines with spaces first to prevent text wrapping issues,
        # but preserve markdown hard line breaks (two spaces followed by newline)
        clean_text = re.sub(r'(?<!  )\n', ' ', clean_text)
        
        # Collapse consecutive spaces/tabs that are not preceding a newline
        clean_text = re.sub(r'[ \t]+(?!\n)', ' ', clean_text)
        # Normalize trailing spaces before newlines: keep exactly two for markdown breaks, remove single spaces
        clean_text = re.sub(r' {2,}\n', '  \n', clean_text)
        clean_text = re.sub(r'(?<! ) \n', '\n', clean_text)
        
        clean_text = clean_text.strip()
        if not clean_text:
            return ''
            
        return '\n\n' + clean_text + '\n\n'


    def convert_hn(self, n, el, text, parent_tags):
        header_text = super().convert_hn(n, el, text, parent_tags)
        if not header_text or not header_text.strip():
            return ''
        return '\n\n' + header_text.strip() + '\n\n'

    def convert_li(self, el, text, parent_tags):
        if not text:
            return ''
        # Strip newlines from list items to keep the list layout compact
        return super().convert_li(el, text.strip('\n'), parent_tags)

    def convert_br(self, el, text, parent_tags):
        # Markdown standard for a hard line break is two spaces followed by a newline
        return '  \n'

    def convert_code(self, el, text, parent_tags):
        if not text:
            return ''
        if 'pre' in parent_tags:
            return text
        # Collapse newlines/spaces for inline code to keep it inline
        return '`' + text.replace('\n', ' ').strip() + '`'

    def convert_b(self, el, text, parent_tags):
        if not text or not text.strip():
            return text
        return f"**{text.strip()}**"

    def convert_strong(self, el, text, parent_tags):
        return self.convert_b(el, text, parent_tags)

    def convert_i(self, el, text, parent_tags):
        if not text or not text.strip():
            return text
        return f"*{text.strip()}*"

    def convert_em(self, el, text, parent_tags):
        return self.convert_i(el, text, parent_tags)

    def convert_del(self, el, text, parent_tags):
        if not text or not text.strip():
            return text
        return f"~~{text.strip()}~~"

    def convert_strike(self, el, text, parent_tags):
        return self.convert_del(el, text, parent_tags)

    def convert_s(self, el, text, parent_tags):
        return self.convert_del(el, text, parent_tags)

    def convert_figcaption(self, el, text, parent_tags):
        if not text or not text.strip():
            return ''
        return f"\n\n*Рисунок: {text.strip()}*\n\n"

    def convert_iframe(self, el, text, parent_tags):
        src = el.get('src')
        if not src:
            return ''
        title = el.get('title', 'Встроенный контент')
        return f"\n\n🔗 [{title}]({src})\n\n"


def clean_url_tracking(url: str) -> str:
    """Strip analytics/UTM tracking parameters from a URL."""
    if not url:
        return url
    try:
        parsed = urlparse(url)
        if parsed.query:
            query_params = parse_qs(parsed.query)
            # List of common tracking parameters to strip
            tracking_keys = [
                'utm_source', 'utm_medium', 'utm_campaign', 'utm_term', 'utm_content',
                'fbclid', 'gclid', 'yclid', 'msclkid', 'mc_cid', 'mc_eid', 'referrer'
            ]
            for key in list(query_params.keys()):
                if key.lower() in tracking_keys or key.lower().startswith('utm_'):
                    query_params.pop(key, None)
            new_query = urlencode(query_params, doseq=True)
            parsed = parsed._replace(query=new_query)
        return urlunparse(parsed)
    except Exception:
        return url


def clean_and_decode_url(url: str) -> str:
    """Clean tracking params and percent-decode double-encoded Cyrillic URLs."""
    if not url:
        return url
    try:
        url = clean_url_tracking(url)
        # Decode percent encoding
        decoded = unquote(url, errors='ignore')
        if '%' in decoded:
            decoded = unquote(decoded, errors='ignore')
        
        # Only strip if it is just '#' or '%23'
        if decoded.strip() == '#' or decoded.strip() == '%23':
            return ''
            
        decoded = decoded.replace(' ', '%20')
        return decoded
    except Exception:
        return url
def extract_real_image_src(img) -> Optional[str]:
    """Extract actual image source from lazy loading attributes or standard src."""
    src_attrs = [
        "data-src", "data-srcset", "data-original", "data-lazy-src",
        "srcset", "src"
    ]
    for attr in src_attrs:
        val = img.get(attr)
        if val:
            val = val.strip()
            if attr in ("srcset", "data-srcset"):
                parts = [p.strip().split()[0] for p in val.split(",") if p.strip()]
                if parts:
                    val = parts[-1]
            if "data:image/gif;base64,R0lGODlhAQ" in val or "transparent.gif" in val:
                continue
            return val
    return None


def merge_split_paragraphs(soup: BeautifulSoup) -> None:
    """
    Finds paragraphs that were split by inline elements (like links or formatting tags)
    at the block level, and merges them back into a single paragraph.
    """
    p_tags = soup.find_all("p")
    decomposed = set()
    
    inline_tag_names = {
        "a", "span", "b", "strong", "i", "em", "code", "del", "strike", 
        "s", "sub", "sup", "ins", "mark", "cite", "q", "dfn", "abbr"
    }
    
    junk_symbol_pattern = re.compile(r'^\[?[→←▲▼⇒⇐≫≪•«»<>–—\s]+\]?$')
    junk_text_pattern = re.compile(
        r'^\[?(Далее|Назад|Читать далее|Дальше|Next|Prev|Previous|Back|Read more|Share|Поделиться|vk|vkontakte|twitter|facebook|telegram|instagram|linkedin|odnoklassniki)\]?$',
        re.IGNORECASE
    )
    
    for p in p_tags:
        if p in decomposed:
            continue
            
        inline_siblings = []
        next_sib = p.next_sibling
        
        while next_sib:
            if next_sib.name is None:  # Text node
                inline_siblings.append(next_sib)
            elif next_sib.name in inline_tag_names:
                inline_siblings.append(next_sib)
            else:
                break
            next_sib = next_sib.next_sibling
            
        if next_sib and next_sib.name == "p" and next_sib not in decomposed:
            has_meaningful_content = False
            full_text = []
            
            for sib in inline_siblings:
                if sib.name is None:
                    txt = str(sib)
                    full_text.append(txt)
                    if txt.strip():
                        has_meaningful_content = True
                else:
                    has_meaningful_content = True
                    full_text.append(sib.get_text())
                    
            if not has_meaningful_content:
                continue
                
            combined_text = "".join(full_text).strip()
            if junk_symbol_pattern.match(combined_text) or junk_text_pattern.match(combined_text):
                continue
                
            # Perform merge
            for sib in inline_siblings:
                p.append(sib)
            for child in list(next_sib.children):
                p.append(child)
                
            decomposed.add(next_sib)
            next_sib.decompose()


def fix_markdown_image_syntax(md: str) -> str:
    r"""
    Validates and repairs image links syntax in Markdown:
    - Automatically closes unclosed parenthesis: ![alt](path -> ![alt](path)
    - Normalizes backslashes to forward slashes: path\to\img -> path/to/img
    """
    pos = 0
    while True:
        idx = md.find("![", pos)
        if idx == -1:
            break
        close_bracket = md.find("]", idx)
        if close_bracket == -1:
            pos = idx + 2
            continue
        if close_bracket + 1 < len(md) and md[close_bracket + 1] == "(":
            next_newline = md.find("\n", close_bracket + 2)
            if next_newline == -1:
                next_newline = len(md)
            next_img = md.find("![", close_bracket + 2)
            if next_img == -1:
                next_img = len(md)
            limit = min(next_newline, next_img)
            
            close_paren = md.find(")", close_bracket + 2)
            if close_paren == -1 or close_paren > limit:
                path_end = limit
                first_space = md.find(" ", close_bracket + 2)
                if first_space != -1 and first_space < path_end:
                    path_end = first_space
                alt = md[idx+2:close_bracket].strip()
                path = md[close_bracket+2:path_end].strip()
                path = path.replace("\\", "/")
                new_link = f"![{alt}]({path})"
                md = md[:idx] + new_link + md[path_end:]
                pos = idx + len(new_link)
            else:
                alt = md[idx+2:close_bracket].strip()
                path = md[close_bracket+2:close_paren].strip()
                path = path.replace("\\", "/")
                new_link = f"![{alt}]({path})"
                md = md[:idx] + new_link + md[close_paren+1:]
                pos = idx + len(new_link)
        else:
            pos = close_bracket + 1
    return md


_LATEX_COMMAND_RE = re.compile(
    r"\\(?:frac|inline|mathcal|mathbf|sqrt|sum|int|prod|begin|text|left|right|"
    r"pm|cdot|times|alpha|beta|gamma|delta|epsilon|sigma|mu|nabla|partial|infty|"
    r"leq|geq|neq|approx|equiv|quad|qquad|mathrm|operatorname|tilde|widetilde|"
    r"rightarrow|leftarrow|Rightarrow|Leftarrow|cdot|ldots|cdots|phi|theta|"
    r"lambda|omega|pi|sum|prod|limits|overline|underline|hat|bar|vec)"
)
_MARKER_HTML_SPAN_RE = re.compile(r'<span id="page-\d+-\d+"></span>\s*', re.IGNORECASE)
_GENERIC_IMAGE_ALTS = frozenset({"image", "img", "figure", "photo", ""})
_REF_FORMULA_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\[(img_ref_\w+)\]")
_DIRECT_FORMULA_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\(([^)\s]+)\)")
_TEXT_MATH_SEGMENT_RE = re.compile(
    r"\\\[.*?\\\]|\\\(.*?\\\)|\$\$.*?\$\$|(?<!\\)\$(?!\$)(?:\\.|[^\$\\])+(?<!\\)\$(?!\$)",
    re.DOTALL,
)
_UNICODE_LATEX_REPLACEMENTS = {
    "⟨": r"\langle ",
    "⟩": r"\rangle ",
    "×": r"\times ",
    "·": r"\cdot ",
    "−": "-",
    "∞": r"\infty ",
    "≤": r"\leq ",
    "≥": r"\geq ",
    "≠": r"\neq ",
    "≈": r"\approx ",
    "±": r"\pm ",
}


def looks_like_latex(text: str) -> bool:
    """Return True when text plausibly contains LaTeX (e.g. marker image alt text)."""
    text = (text or "").strip()
    if not text or text.lower() in _GENERIC_IMAGE_ALTS:
        return False
    lower = text.lower()
    if lower.startswith(("data:", "http://", "https://", "file://")):
        return False
    if text.startswith("\\$"):
        return False
    if text.startswith("\\"):
        return True
    if _LATEX_COMMAND_RE.search(text):
        return True
    if re.search(r"[_^]\{", text):
        return True
    if re.search(r"[_^{]", text) and ("\\" in text or re.search(r"[A-Za-z]_\{", text)):
        return True
    if "^" in text and re.search(r"[A-Za-z]", text) and ("=" in text or "\\" in text):
        return True
    if re.search(r"[A-Za-z]_[A-Za-z0-9]", text) and len(text) <= 12:
        return True
    return False


def normalize_marker_latex(latex: str) -> str:
    """Normalize LaTeX emitted by marker-pdf before conversion."""
    latex = (latex or "").strip()
    latex = re.sub(r"^\\inline\s+", "", latex)
    latex = latex.replace("—", "-").replace("–", "-")
    if latex.startswith("$$") and latex.endswith("$$"):
        latex = latex[2:-2].strip()
    elif latex.startswith("$") and latex.endswith("$"):
        latex = latex[1:-1].strip()
    return latex


def repair_marker_latex(latex: str) -> str:
    """Fix common marker-pdf OCR/transcription mistakes in LaTeX."""
    latex = normalize_marker_latex(latex)
    latex = re.sub(r"\\langle\s*/\\text\{", r"\\langle \\text{", latex)
    latex = re.sub(r"\\langle\s*/", r"\\langle ", latex)
    latex = re.sub(r"\\text\{\s+", r"\\text{", latex)
    latex = re.sub(r"\s+\}", "}", latex)
    latex = re.sub(r"\\qquad\s+\\qquad", r"\\qquad ", latex)
    latex = re.sub(r"\s*,\s*\\,", r", \\,", latex)
    return latex.strip()


def infer_latex_alt_from_image_name(img_name: str) -> Optional[str]:
    """Infer LaTeX alt text from a marker image filename when the alt is empty."""
    stem = Path(unquote(img_name)).stem.strip()
    if stem.startswith("\\"):
        return stem
    if _LATEX_COMMAND_RE.search(stem):
        return stem
    if re.search(r"[_^]\{", stem):
        return stem
    return None


def classify_pdf_image_role(img_obj, img_name: str = "") -> str:
    """
    Classify a marker-pdf raster image by dimensions for alt-text enrichment.

    Returns one of: formula-inline, formula-display, figure, spacer.
    """
    try:
        width, height = img_obj.size
    except Exception:
        return "figure"

    if width <= 16 and height <= 16:
        return "spacer"

    name_lower = (img_name or "").lower()
    if any(k in name_lower for k in ("eq", "formula", "math", "equation")):
        return "formula-inline" if height <= 120 else "formula-display"

    aspect = width / max(height, 1)
    area = width * height

    if height <= 80 and aspect >= 1.5:
        return "formula-inline"
    if height <= 120 and width <= 600 and aspect >= 0.8:
        return "formula-inline"
    if height <= 200 and width <= 800 and area < 200_000:
        if height > 80 or aspect < 1.5:
            return "formula-display"

    return "figure"


def enrich_image_alt_text(alt: str, img_name: str, img_obj=None) -> str:
    """Fill empty or generic image alt text from filename and dimension heuristics."""
    alt = (alt or "").strip()
    if alt and alt.lower() not in _GENERIC_IMAGE_ALTS:
        return alt
    inferred = infer_latex_alt_from_image_name(img_name)
    if inferred:
        return inferred
    if img_obj is not None:
        role = classify_pdf_image_role(img_obj, img_name)
        if role in ("formula-inline", "formula-display"):
            return role
        if role == "figure":
            return "figure"
    return alt


def strip_marker_html_artifacts(md_content: str) -> str:
    """Remove marker-pdf HTML anchor spans embedded in markdown."""
    return _MARKER_HTML_SPAN_RE.sub("", md_content)


def _protect_code_regions(md_content: str) -> tuple[str, dict[str, str]]:
    """Temporarily replace fenced/inline code so math passes do not touch it."""
    placeholders: dict[str, str] = {}

    def _store(match: re.Match) -> str:
        key = f"CODEPROTECT{len(placeholders)}"
        placeholders[key] = match.group(0)
        return key

    protected = re.sub(r"```[\s\S]*?```", _store, md_content)
    protected = re.sub(r"`[^`\n]+`", _store, protected)
    protected = re.sub(r"<pre[\s\S]*?</pre>", _store, protected, flags=re.IGNORECASE)
    protected = re.sub(r"<code[\s\S]*?</code>", _store, protected, flags=re.IGNORECASE)
    return protected, placeholders


def _restore_code_regions(md_content: str, placeholders: dict[str, str]) -> str:
    restored = md_content
    for key, original in placeholders.items():
        restored = restored.replace(key, original)
    return restored


def repair_math_delimiters_in_markdown(md_content: str) -> str:
    """Repair LaTeX inside existing $...$ and $$...$$ markdown delimiters."""
    def _repair_block(match: re.Match) -> str:
        return f"$${repair_marker_latex(match.group(1))}$$"

    def _repair_inline(match: re.Match) -> str:
        inner = match.group(1)
        if inner.startswith("\\$"):
            return match.group(0)
        return f"${repair_marker_latex(inner)}$"

    content = re.sub(r"\$\$(.*?)\$\$", _repair_block, md_content, flags=re.DOTALL)
    content = re.sub(
        r"(?<!\$)\$(?!\$)([^\$\n]+?)(?<!\$)\$(?!\$)",
        _repair_inline,
        content,
    )
    return content


def sanitize_latex_for_conversion(latex: str) -> str:
    """Apply additional cleanup before handing LaTeX to latex2mathml."""
    latex = repair_marker_latex(latex)
    for src, dst in _UNICODE_LATEX_REPLACEMENTS.items():
        latex = latex.replace(src, dst)
    latex = re.sub(r"\s+", " ", latex).strip()
    latex = re.sub(r"\(\s+", "(", latex)
    latex = re.sub(r"\s+\)", ")", latex)
    return latex


def extract_latex_from_image_attrs(img) -> Optional[str]:
    """Return LaTeX from img alt/title/source when it looks like a formula."""
    for attr in ("source", "alt", "title"):
        value = (img.get(attr) or "").strip()
        if looks_like_latex(value):
            return value
    return None


def _latex_delimiter_for_recovered(latex: str) -> str:
    """Wrap recovered LaTeX in inline or display markdown delimiters."""
    latex = normalize_marker_latex(latex)
    if is_display_latex(latex):
        return f"\n$${latex}$$\n"
    return f" ${latex}$ "


def is_display_latex(latex: str) -> bool:
    """Heuristic: display math vs inline math for recovered marker formulas."""
    normalized = normalize_marker_latex(latex)
    if r"\begin{" in normalized:
        return True
    if "\\\\" in normalized:
        return True
    if "=" in normalized:
        return True
    if len(normalized) > 80:
        return True
    return False


def recover_formula_images_in_markdown(md_content: str) -> tuple[str, set[str]]:
    """
    Replace marker-style formula images with $/$$ delimiters.

    Handles both reference links (``![LaTeX][img_ref_N]``) and direct image paths
    (``![LaTeX](figure-0.png)``). Returns updated markdown and removed img_ref ids.
    """
    removed_refs: set[str] = set()

    def _repl_ref(match: re.Match) -> str:
        alt = match.group(1)
        ref_id = match.group(2)
        if not looks_like_latex(alt):
            return match.group(0)
        removed_refs.add(ref_id)
        return _latex_delimiter_for_recovered(alt)

    def _repl_direct(match: re.Match) -> str:
        alt = match.group(1)
        if not looks_like_latex(alt):
            return match.group(0)
        return _latex_delimiter_for_recovered(alt)

    updated = _REF_FORMULA_IMAGE_RE.sub(_repl_ref, md_content)
    updated = _DIRECT_FORMULA_IMAGE_RE.sub(_repl_direct, updated)
    for ref_id in removed_refs:
        updated = re.sub(rf"(?m)^\[{re.escape(ref_id)}\]:.*\n?", "", updated)
    return updated, removed_refs


def collapse_split_display_math(md_content: str) -> str:
    """Merge adjacent display-math blocks and drop empty $$ pairs split by marker."""
    content, placeholders = _protect_code_regions(md_content)
    # Line-only empty blocks; avoid matching closing+opening delimiters of adjacent math.
    content = re.sub(r"(?m)^\$\$\s*\$\$\s*$", "", content)

    prev = None
    while prev != content:
        prev = content
        content = re.sub(
            r"\$\$(.*?)\$\$\s+\$\$(.*?)\$\$",
            lambda m: f"$${m.group(1).strip()} {m.group(2).strip()}$$",
            content,
            flags=re.DOTALL,
        )

    return _restore_code_regions(content, placeholders)


def remove_unused_img_ref_definitions(md_content: str) -> str:
    """Drop orphan ``[img_ref_N]:`` definitions not referenced in markdown."""
    used_refs = set(re.findall(r"!\[[^\]]*\]\[(img_ref_\w+)\]", md_content))

    def _keep_or_drop(match: re.Match) -> str:
        ref_id = match.group(1)
        return match.group(0) if ref_id in used_refs else ""

    return re.sub(r"(?m)^\[(img_ref_\w+)\]:.*\n?", _keep_or_drop, md_content)


def _image_path_candidates(img_name: str) -> list[str]:
    """Build path variants for matching marker image links in markdown."""
    candidates: list[str] = []
    seen: set[str] = set()
    for raw in (img_name, Path(img_name).name, unquote(img_name), unquote(Path(img_name).name)):
        normalized = raw.replace("\\", "/")
        for variant in (normalized, quote(normalized, safe="/")):
            if variant and variant not in seen:
                seen.add(variant)
                candidates.append(variant)
    return candidates


def embed_pdf_images_in_markdown(
    pdf_markdown: str,
    images: dict,
) -> tuple[str, dict[str, dict[str, Any]]]:
    """
    Embed marker-pdf images as base64 reference definitions.

    Returns updated markdown and per-ref metadata (role, dimensions, source name).
    Only referenced images receive definition lines at the document tail.
    """
    import base64
    import io

    image_roles: dict[str, dict[str, Any]] = {}
    ref_by_image_key: dict[str, str] = {}
    image_refs: dict[str, str] = {}

    for idx, (img_name, img_obj) in enumerate(images.items()):
        ref_id = f"img_ref_{idx}"
        ref_by_image_key[img_name] = ref_id

        try:
            width, height = img_obj.size
        except Exception:
            width, height = None, None

        image_roles[ref_id] = {
            "role": classify_pdf_image_role(img_obj, img_name),
            "width": width,
            "height": height,
            "source_name": img_name,
        }

        buffered = io.BytesIO()
        img_format = img_obj.format if img_obj.format else "JPEG"
        working_img = img_obj
        if working_img.mode != "RGB" and img_format in ("JPEG", "JPG"):
            working_img = working_img.convert("RGB")
        try:
            from PIL import ImageFile

            ImageFile.LOAD_TRUNCATED_IMAGES = True
            working_img.load()
            working_img.save(buffered, format=img_format)
        except Exception as save_err:
            logger.warning(
                f"Failed to save image {img_name} as {img_format}: {save_err}. Falling back to PNG."
            )
            buffered = io.BytesIO()
            img_format = "PNG"
            try:
                if working_img.mode not in ("RGB", "RGBA"):
                    working_img = working_img.convert(
                        "RGBA"
                        if "transparency" in working_img.info or working_img.mode == "P"
                        else "RGB"
                    )
                working_img.save(buffered, format="PNG")
            except Exception as png_err:
                logger.error(f"Failed to save image {img_name} as PNG fallback: {png_err}")
                continue

        img_b64 = base64.b64encode(buffered.getvalue()).decode("utf-8")
        mime_type = f"image/{img_format.lower()}"
        if mime_type == "image/jpg":
            mime_type = "image/jpeg"
        image_refs[ref_id] = f"data:{mime_type};base64,{img_b64}"

    matched_image_keys: set[str] = set()
    for img_name, img_obj in images.items():
        ref_id = ref_by_image_key[img_name]
        candidates = _image_path_candidates(img_name)
        full_path = candidates[0] if candidates else img_name

        for candidate in candidates:
            escaped_name = re.escape(candidate)
            if candidate == full_path:
                pattern = rf"!\[(.*?)\]\({escaped_name}\)"
            else:
                pattern = rf"!\[(.*?)\]\((?:{escaped_name}|[^)]*/{escaped_name})\)"

            def _replace_with_ref(match, _ref_id=ref_id, _img_name=img_name, _img_obj=img_obj):
                alt = enrich_image_alt_text(match.group(1), _img_name, _img_obj)
                return f"![{alt}][{_ref_id}]"

            pdf_markdown, replacements = re.subn(
                pattern,
                _replace_with_ref,
                pdf_markdown,
            )
            if replacements:
                matched_image_keys.add(img_name)
                break

    for unmatched_key in set(images.keys()) - matched_image_keys:
        logger.warning(f"PDF image key not matched in markdown: {unmatched_key}")

    used_refs = set(re.findall(r"!\[[^\]]*\]\[(img_ref_\w+)\]", pdf_markdown))
    if used_refs:
        pdf_markdown += "\n\n"
        for ref_id, b64_uri in image_refs.items():
            if ref_id in used_refs:
                pdf_markdown += f"[{ref_id}]: {b64_uri}\n"

    return pdf_markdown, image_roles


def _parse_text_math_segment(segment: str) -> Optional[tuple[str, bool]]:
    """Parse a math delimiter segment into (latex, is_display), or None if not math."""
    if segment.startswith(r"\["):
        return segment[2:-2].strip(), True
    if segment.startswith(r"\("):
        return segment[2:-2].strip(), False
    if segment.startswith("$$"):
        return segment[2:-2].strip(), True
    if segment.startswith("$") and segment.endswith("$") and len(segment) >= 2:
        inner = segment[1:-1].strip()
        if inner.startswith("\\$"):
            return None
        if not looks_like_latex(inner) and not re.search(r"[\\_{^=]", inner):
            return None
        return inner, False
    return None


def _convert_text_math_segments(
    soup: BeautifulSoup,
    text_node,
    text_str: str,
    to_markdown: bool,
) -> None:
    """Split a text node on math delimiters and replace with MathML or $ delimiters."""
    matches = list(_TEXT_MATH_SEGMENT_RE.finditer(text_str))
    if not matches:
        return

    new_nodes = []
    last_end = 0
    for match in matches:
        if match.start() > last_end:
            new_nodes.append(soup.new_string(text_str[last_end:match.start()]))

        segment = match.group(0)
        parsed = _parse_text_math_segment(segment)
        if not parsed:
            new_nodes.append(soup.new_string(segment))
        else:
            latex, is_display = parsed
            if to_markdown:
                math_text = f" $${latex}$$ " if is_display else f" ${latex}$ "
                new_nodes.append(soup.new_string(math_text))
            else:
                math_soup = convert_latex_to_mathml(latex, "block" if is_display else "inline")
                new_math = math_soup.find("math")
                if new_math:
                    new_nodes.append(new_math)
                else:
                    fallback = soup.new_tag("span", attrs={"class": "math-fallback"})
                    fallback["data-latex"] = latex
                    fallback.string = f" $${latex}$$ " if is_display else f" ${latex}$ "
                    new_nodes.append(fallback)
        last_end = match.end()

    if last_end < len(text_str):
        new_nodes.append(soup.new_string(text_str[last_end:]))

    for node in new_nodes:
        text_node.insert_before(node)
    text_node.decompose()


def _replace_latex_node(
    soup: BeautifulSoup,
    node,
    latex_code: str,
    is_display: bool,
    to_markdown: bool,
) -> None:
    """Replace an HTML node with Markdown math text or MathML."""
    latex_code = normalize_marker_latex(latex_code)
    if to_markdown:
        math_text = f" $${latex_code}$$ " if is_display else f" ${latex_code}$ "
        node.replace_with(soup.new_string(math_text))
        return

    math_soup = convert_latex_to_mathml(latex_code, "block" if is_display else "inline")
    new_math = math_soup.find("math")
    if new_math:
        node.replace_with(new_math)
        return

    fallback_span = soup.new_tag("span", attrs={"class": "math-fallback"})
    fallback_span["data-latex"] = latex_code
    fallback_span.string = f" $${latex_code}$$ " if is_display else f" ${latex_code}$ "
    node.replace_with(fallback_span)


def convert_latex_to_mathml(latex_code: str, display: str = "inline") -> BeautifulSoup:
    """Converts LaTeX formula string to namespaced MathML BeautifulSoup structure."""
    candidates = []
    repaired = repair_marker_latex(latex_code)
    sanitized = sanitize_latex_for_conversion(latex_code)
    for candidate in (repaired, sanitized):
        if candidate and candidate not in candidates:
            candidates.append(candidate)

    last_error: Optional[Exception] = None
    for candidate in candidates:
        try:
            mathml_str = latex2mathml.converter.convert(candidate)
            math_soup = BeautifulSoup(mathml_str, "xml")
            math_tag = math_soup.find("math")
            if math_tag:
                math_tag["display"] = display
                math_tag["xmlns"] = "http://www.w3.org/1998/Math/MathML"
                return math_soup
        except Exception as e:
            last_error = e
            logger.debug(f"LaTeX conversion attempt failed for {candidate!r}: {e}")

    logger.error(f"Failed to convert LaTeX to MathML after {len(candidates)} attempts: {last_error}")
    safe = html.escape(sanitized or normalized, quote=True)
    fallback_body = html.escape(sanitized or normalized)
    fallback = BeautifulSoup(
        f'<span class="math-fallback" data-latex="{safe}">{fallback_body}</span>',
        "xml",
    )
    return fallback


def convert_mathml_to_latex(mathml_tag) -> str:
    """Converts MathML Tag back to LaTeX formula string."""
    try:
        mathml_str = str(mathml_tag)
        latex = MathMLToLaTeX.convert(mathml_str)
        return latex
    except Exception as e:
        logger.error(f"Failed to convert MathML to LaTeX: {e}")
        return mathml_tag.get_text()


def preprocess_formulas(soup: BeautifulSoup, to_markdown: bool = False) -> None:
    """
    Finds and normalizes math formulas (KaTeX, MathJax, MathML, raw TeX delimiters, formula images) in the HTML soup.
    - If to_markdown: converts them into Markdown delimiters $...$ and $$...$$.
    - If not to_markdown (EPUB): converts them into clean, namespaced MathML structures.
    """
    # 0. Convert semantic math wrappers emitted by md_to_html_fallback
    for el in soup.find_all(class_=re.compile(r"\bmath-inline\b")):
        latex_code = (el.get("data-latex") or "").strip()
        if not latex_code:
            continue
        if to_markdown:
            el.replace_with(soup.new_string(f" ${latex_code}$ "))
        else:
            math_soup = convert_latex_to_mathml(latex_code, "inline")
            new_math = math_soup.find("math")
            if new_math:
                el.replace_with(new_math)
            else:
                fallback_span = soup.new_tag("span", attrs={"class": "math-fallback"})
                fallback_span.string = f" ${latex_code}$ "
                el.replace_with(fallback_span)

    for el in soup.find_all(class_=re.compile(r"\bmath-block\b")):
        latex_code = (el.get("data-latex") or "").strip()
        if not latex_code:
            continue
        if to_markdown:
            el.replace_with(soup.new_string(f" $${latex_code}$$ "))
        else:
            math_soup = convert_latex_to_mathml(latex_code, "block")
            new_math = math_soup.find("math")
            if new_math:
                el.replace_with(new_math)
            else:
                fallback_span = soup.new_tag("span", attrs={"class": "math-fallback"})
                fallback_span.string = f" $${latex_code}$$ "
                el.replace_with(fallback_span)

    # 0.05 Retry math-fallback spans that still carry recoverable LaTeX
    for el in list(soup.find_all(class_="math-fallback")):
        latex_code = (el.get("data-latex") or el.get_text() or "").strip()
        if not latex_code:
            continue
        is_display = is_display_latex(latex_code)
        _replace_latex_node(soup, el, latex_code, is_display, to_markdown)

    # 0.1 Convert formula images (class-based or LaTeX in alt/title from marker-pdf)
    for img in list(soup.find_all("img")):
        classes = img.get("class", [])
        if isinstance(classes, str):
            classes = [classes]

        latex_code = None
        is_display = False

        if any("formula" in c for c in classes) or any("katex" in c for c in classes) or any("math" in c for c in classes):
            latex_code = extract_latex_from_image_attrs(img) or img.get("source") or img.get("alt")
            if latex_code:
                latex_code = latex_code.strip()
                is_display = "inline" not in classes
                if latex_code.startswith("$$") and latex_code.endswith("$$"):
                    latex_code = latex_code[2:-2].strip()
                    is_display = True
                elif latex_code.startswith("$") and latex_code.endswith("$"):
                    latex_code = latex_code[1:-1].strip()
                    is_display = False
        else:
            latex_code = extract_latex_from_image_attrs(img)
            if latex_code:
                is_display = is_display_latex(latex_code)

        if latex_code:
            _replace_latex_node(soup, img, latex_code, is_display, to_markdown)

    # 1. Convert KaTeX elements (usually <span class="katex"> or <div class="katex-display">)
    for el in soup.find_all(class_=re.compile(r"\bkatex\b|\bkatex-display\b")):
        if el.find_parent(class_=re.compile(r"\bkatex\b|\bkatex-display\b")):
            continue
            
        is_display = "katex-display" in el.get("class", []) or el.name == "div"
        parent_display = el.find_parent(class_=re.compile(r"display"))
        if parent_display:
            is_display = True
            
        ann = el.find("annotation", encoding="application/x-tex")
        latex_code = ann.string.strip() if ann and ann.string else None
        math_tag = el.find("math")
        
        if to_markdown:
            if latex_code:
                math_text = f" $${latex_code}$$ " if is_display else f" ${latex_code}$ "
                el.replace_with(soup.new_string(math_text))
            elif math_tag:
                latex_from_mml = convert_mathml_to_latex(math_tag)
                math_text = f" $${latex_from_mml}$$ " if is_display else f" ${latex_from_mml}$ "
                el.replace_with(soup.new_string(math_text))
            else:
                el.decompose()
        else:
            if math_tag:
                math_tag["xmlns"] = "http://www.w3.org/1998/Math/MathML"
                math_tag["display"] = "block" if is_display else "inline"
                extracted_math = math_tag.extract()
                el.replace_with(extracted_math)
            elif latex_code:
                math_soup = convert_latex_to_mathml(latex_code, "block" if is_display else "inline")
                new_math = math_soup.find("math")
                if new_math:
                    el.replace_with(new_math)
                else:
                    fallback_span = soup.new_tag("span", attrs={"class": "math-fallback"})
                    fallback_span.string = f" $${latex_code}$$ " if is_display else f" ${latex_code}$ "
                    el.replace_with(fallback_span)

    # 2. Process MathJax script tags (<script type="math/tex">)
    for script in soup.find_all("script", type=re.compile(r"^math/tex")):
        latex_code = script.string.strip() if script.string else ""
        is_display = "mode=display" in script.get("type", "")
        
        if to_markdown:
            math_text = f" $${latex_code}$$ " if is_display else f" ${latex_code}$ "
            script.replace_with(soup.new_string(math_text))
        else:
            math_soup = convert_latex_to_mathml(latex_code, "block" if is_display else "inline")
            new_math = math_soup.find("math")
            if new_math:
                script.replace_with(new_math)
            else:
                script.decompose()

    # 3. Process standalone <math> tags
    for math_tag in soup.find_all("math"):
        if math_tag.find_parent(class_=re.compile(r"\bkatex\b")):
            continue
            
        is_display = math_tag.get("display") == "block"
        
        if to_markdown:
            latex_code = convert_mathml_to_latex(math_tag)
            math_text = f" $${latex_code}$$ " if is_display else f" ${latex_code}$ "
            math_tag.replace_with(soup.new_string(math_text))
        else:
            math_tag["xmlns"] = "http://www.w3.org/1998/Math/MathML"
            if is_display:
                math_tag["display"] = "block"
            else:
                math_tag["display"] = "inline"

    # 4. Process text nodes containing \( \), \[ \], $$ $$, and inline $...$
    for text_node in list(soup.find_all(string=True)):
        parent = text_node.parent
        if parent and parent.name in ("script", "style", "code", "pre", "math"):
            continue
        if parent and parent.get("class") and any(
            c in ("katex", "math-inline", "math-block", "math-fallback")
            for c in parent.get("class", [])
        ):
            continue

        text_str = str(text_node)
        if not text_str.strip():
            continue

        _convert_text_math_segments(soup, text_node, text_str, to_markdown)


def is_formula_img(img) -> bool:
    if extract_latex_from_image_attrs(img):
        return True

    keywords = ['math', 'tex', 'eq', 'formula', 'katex']
    # Check classes
    classes = img.get("class", [])
    if isinstance(classes, str):
        classes = [classes]
    if any(any(kw in c.lower() for kw in keywords) for c in classes):
        return True
    # Check other attributes
    for attr, val in img.attrs.items():
        val_str = ""
        if isinstance(val, list):
            val_str = " ".join(val)
        elif isinstance(val, str):
            val_str = val
        if val_str:
            if val_str.startswith("data:"):
                # Avoid scanning base64 payload to prevent false positives (e.g. 'eq' in base64 payload)
                header = val_str.split(",", 1)[0]
                if any(kw in header.lower() for kw in keywords):
                    return True
                continue
            if any(kw in val_str.lower() for kw in keywords):
                return True
    return False


def infer_image_role_from_tag(img) -> str:
    """
    Infers the role of the image from its class, id, src, or alt attributes.
    """
    classes = img.get("class", [])
    if isinstance(classes, list):
        classes = " ".join(classes)
    elif not isinstance(classes, str):
        classes = str(classes)
        
    img_id = str(img.get("id", ""))
    src = str(img.get("src", ""))
    if src.startswith("data:"):
        src = src.split(",", 1)[0]
    alt = str(img.get("alt", ""))
    
    combined = (classes + " " + img_id + " " + src + " " + alt).lower()
    
    if any(
        k in combined
        for k in [
            "formula-inline",
            "formula-display",
            "formula",
            "math",
            "tex",
            "katex",
            "equation",
        ]
    ):
        return "formula"
    if any(k in combined for k in ["chart", "plot", "graph", "diagram", "figure"]):
        return "chart"
    if "table" in combined:
        return "table"
    if "screenshot" in combined:
        return "screenshot"
    return "photo"


def is_decorative_svg(svg) -> bool:
    """Check if an SVG tag is decorative (e.g. tiny icons or explicitly hidden)."""
    width_val = svg.get("width")
    height_val = svg.get("height")
    has_viewbox = bool(svg.get("viewBox"))
    is_aria_hidden = svg.get("aria-hidden") == "true"
    role = svg.get("role")
    
    has_text = bool(svg.get_text(strip=True))
    has_title = bool(svg.find("title"))
    has_desc = bool(svg.find("desc"))
    
    classes = svg.get("class", [])
    if isinstance(classes, list):
        classes = " ".join(classes)
    elif not isinstance(classes, str):
        classes = str(classes)
    classes_lower = classes.lower()
    
    img_id = str(svg.get("id", "")).lower()
    aria_label = str(svg.get("aria-label", "")).lower()
    combined = classes_lower + " " + img_id + " " + aria_label
    
    meaningful_kw = ["chart", "plot", "graph", "diagram", "figure", "math", "formula", "equation"]
    has_meaningful_kw = any(k in combined for k in meaningful_kw)
    is_icon_class = any(k in combined for k in ["icon", "logo", "social"])
    
    try:
        w = int(float(width_val)) if width_val else None
        h = int(float(height_val)) if height_val else None
        if w is not None and h is not None and w <= 24 and h <= 24:
            if not (has_title or has_desc or has_meaningful_kw) or is_icon_class:
                return True
    except ValueError:
        pass

    if is_aria_hidden and not (has_title or has_desc or has_meaningful_kw):
        return True
        
    if role != "img" and not has_text and not has_title and not has_desc and not has_viewbox and not has_meaningful_kw:
        return True
        
    if is_icon_class and not (has_title or has_desc or has_meaningful_kw):
        return True
        
    return False


def sanitize_svg(svg) -> None:
    """Sanitize SVG content by unlinking script tags and dynamic event attributes."""
    for script in svg.find_all("script"):
        script.decompose()
        
    for element in [svg] + svg.find_all():
        attrs_to_remove = []
        for attr, val in element.attrs.items():
            if attr.lower().startswith("on"):
                attrs_to_remove.append(attr)
            elif isinstance(val, str) and val.lower().startswith("javascript:"):
                if attr.lower() in ("href", "xlink:href", "src", "action"):
                    element[attr] = "#"
                else:
                    attrs_to_remove.append(attr)
                    
        for attr in attrs_to_remove:
            del element[attr]


def remove_or_preserve_svg(soup: BeautifulSoup) -> None:
    """
    Remove only decorative tiny icon SVGs, keep and sanitize meaningful SVGs.
    """
    for svg in soup.find_all("svg"):
        if is_decorative_svg(svg):
            svg.decompose()
        else:
            sanitize_svg(svg)




def validate_and_normalize_math(md_content: str) -> str:
    """
    Validate and normalize mathematical formulas to be standard Pandoc markdown compliant.
    Recovers formula images, converts TeX delimiters, and repairs marker LaTeX artifacts.
    """
    content, code_placeholders = _protect_code_regions(md_content)
    content, _ = recover_formula_images_in_markdown(content)
    content = re.sub(r"\\\[(.*?)\\\]", r"$$\1$$", content, flags=re.DOTALL)
    content = re.sub(r"\\\((.*?)\\\)", r"$\1$", content, flags=re.DOTALL)
    content = repair_math_delimiters_in_markdown(content)
    content = _restore_code_regions(content, code_placeholders)
    return content


def normalize_pdf_markdown(md_content: str) -> str:
    """
    Full PDF markdown post-processing for faithful formula transcription.

    Intended to run after marker-pdf conversion and image-reference embedding.
    """
    content = strip_marker_html_artifacts(md_content)
    content = collapse_split_display_math(content)
    content = validate_and_normalize_math(content)
    content = remove_unused_img_ref_definitions(content)
    return content


def preprocess_html(html: str) -> str:
    """Preprocess HTML to decompose useless elements, strip tracking, and unwrap meaningless tags."""
    soup = BeautifulSoup(html, "lxml")
    
    # 1. Strip useless tags completely
    useless_tags = ["script", "style", "meta", "noscript"]
    if config.DISABLE_IMAGES:
        useless_tags.append("img")
        
    for tag in soup.find_all(useless_tags):
        # DO NOT decompose MathJax/KaTeX scripts
        if tag.name == "script":
            script_type = tag.get("type", "")
            if script_type and ("math/tex" in script_type or "math/mml" in script_type):
                continue
        # DO NOT decompose math formula images
        if tag.name == "img":
            if is_formula_img(tag):
                continue
        tag.decompose()
        
    # Preserve meaningful SVGs, remove decorative ones
    remove_or_preserve_svg(soup)

        
    # 1.1 Strip cookie consent banners / CMP overlays (e.g. OneTrust, Didomi, Cookiebot, etc.)
    consent_selectors = [
        '#onetrust-consent-sdk', '#onetrust-banner-sdk', '.onetrust-pc-dark',
        '#didomi-host', '.didomi-popup', '.didomi-consent-popup',
        '#CybotCookiebotDialog', '#cookiebot',
        '#qc-cmp2-container', '#qc-cmp2-ui',
        '#consent_blackbar', '#truste-consent-track',
        '.cookie-consent', '.cookieconsent', '.cc-window', '.cc-banner', '.cc-type-info',
        '#cookie-law-info-bar', '#cookie-law-info-again',
        '#sp-consent-container', '.cookie-notice-container', '.cookie-notice',
        '#gdpr-consent-tool-wrapper', '#gdpr-consent-banner',
        '.cookie-banner', '.cookie-popup', '.cookie-dialog', '.cookie-bar', '.cookiebar',
        '#privacy-consent', '#cookie-consent-banner', '.js-cookie-consent',
        '[role="dialog"][aria-label*="cookie" i]', '[role="dialog"][aria-label*="consent" i]',
        '[role="dialog"][aria-labelledby*="cookie" i]', '[role="dialog"][aria-labelledby*="consent" i]',
        '[role="alertdialog"][aria-label*="cookie" i]', '[role="alertdialog"][aria-label*="consent" i]',
        '[role="alertdialog"][aria-labelledby*="cookie" i]', '[role="alertdialog"][aria-labelledby*="consent" i]'
    ]
    for selector in consent_selectors:
        for tag in soup.select(selector):
            tag.decompose()
            
    # 1.2 Strip page-related span elements that are not parsed correctly by some readers
    for tag in soup.find_all("span", id=re.compile(r"^page-")):
        tag.decompose()
        
    # 2. Clean links and remove tracking params
    for a in soup.find_all("a"):
        href = a.get("href")
        if href:
            a["href"] = clean_and_decode_url(href)
        # Decompose empty links with no text or inner tags
        if not a.get_text(strip=True) and not a.find_all():
            a.decompose()
            
    # 3. Clean up span/div wrappers that have no attributes/styling
    for tag in soup.find_all(["span", "div"]):
        if not tag.has_attr("class") and not tag.has_attr("id") and not tag.has_attr("style"):
            tag.unwrap()
            
    # 4. Remove empty paragraphs, empty blockquotes, and block elements containing only navigation junk
    junk_block_pattern = re.compile(r'^\[?[→←▲▼⇒⇐≫≪•«»<>–—\s]+\]?$')
    for tag in soup.find_all(["p", "blockquote", "h1", "h2", "h3", "h4", "h5", "h6"]):
        text = tag.get_text().replace('\xa0', ' ').replace('&nbsp;', ' ').strip()
        if (not text and not tag.find_all()) or junk_block_pattern.match(text):
            tag.decompose()
            
    # 5. Wrap loose block-level links in <p> tags so they are processed as separate blocks/paragraphs
    block_parents = {"div", "body", "section", "article", "html", "[document]"}
    for a in soup.find_all("a"):
        parent = a.parent
        is_loose = True
        while parent:
            if parent.name in {"p", "h1", "h2", "h3", "h4", "h5", "h6", "li", "blockquote", "td", "th"}:
                is_loose = False
                break
            if parent.name in block_parents:
                parent = parent.parent
            else:
                is_loose = False
                break
        if is_loose:
            # Check siblings: if the parent contains any non-empty text or inline tags, this is an inline link context
            has_inline_context = False
            for sib in a.parent.children:
                if sib == a:
                    continue
                if sib.name is None:
                    if str(sib).strip():
                        has_inline_context = True
                        break
                elif sib.name in {"a", "span", "b", "strong", "i", "em", "code", "del", "strike", "s"}:
                    has_inline_context = True
                    break
            if not has_inline_context:
                a.wrap(soup.new_tag("p"))

    # 6. Merge split paragraphs
    merge_split_paragraphs(soup)

    # 7. Security hardening: strip event-handler attributes and javascript: URIs
    _JS_SCHEME = re.compile(r'^\s*javascript\s*:', re.IGNORECASE)
    for tag in soup.find_all(True):
        # Remove all on* event handler attributes
        for attr in list(tag.attrs.keys()):
            if attr.lower().startswith("on"):
                del tag[attr]
        # Neutralize javascript: href / src
        for url_attr in ("href", "src", "action"):
            val = tag.get(url_attr, "")
            if val and _JS_SCHEME.match(str(val)):
                tag[url_attr] = "#"

    return str(soup)


def collapse_links(md: str) -> str:
    """Collapse line-break links where text, URL or brackets are split across lines."""
    def replace_link(match):
        text = match.group(1)
        url = match.group(2)
        clean_text = re.sub(r'\s+', ' ', text).strip()
        
        # Check if there is a title part inside the URL parenthesis (e.g. url "title")
        url_stripped = url.strip()
        quote_match = re.search(r'["\']', url_stripped)
        if quote_match:
            quote_idx = quote_match.start()
            url_part = url_stripped[:quote_idx].strip()
            title_part = url_stripped[quote_idx:].strip()
            
            clean_url_part = re.sub(r'\s+', '', url_part)
            clean_title_part = re.sub(r'\s+', ' ', title_part)
            clean_url = f'{clean_url_part} {clean_title_part}'
        else:
            clean_url = re.sub(r'\s+', '', url_stripped)
            
        return f"[{clean_text}]({clean_url})"
    return re.sub(r'\[([^\]]*?)\]\s*\(\s*([^\)]*?)\s*\)', replace_link, md, flags=re.DOTALL)


def fix_lists_and_links(md: str) -> str:
    """Convert consecutive lines of standalone links into standard bullet lists, removing empty lines between them."""
    lines = md.split('\n')
    new_lines = []
    link_pattern = re.compile(r'(\[[^\]]+?\]\([^\)]+?\))')
    
    for line in lines:
        stripped = line.strip()
        if not stripped:
            new_lines.append(line)
            continue
            
        links = link_pattern.findall(line)
        if len(links) >= 2:
            # Check if line consists only of these links and whitespace
            temp_line = line
            for link in links:
                temp_line = temp_line.replace(link, '', 1)
            if not temp_line.strip():
                # Get the indentation of the original line
                indent = len(line) - len(line.lstrip())
                for link in links:
                    new_lines.append(' ' * indent + f"* {link}")
                continue
                
        new_lines.append(line)
        
    lines = new_lines
    
    # Identify indices of non-empty lines
    non_empty_indices = [i for i, line in enumerate(lines) if line.strip() != '']
    if not non_empty_indices:
        return md
        
    # Find which of those non-empty lines are link-only
    link_only_indices = []
    for idx in non_empty_indices:
        stripped = lines[idx].strip()
        if re.match(r'^\[[^\]]+?\]\([^\)]+?\)[.,!?;]*$', stripped):
            link_only_indices.append(idx)
            
    # Group them if they are consecutive in the list of non-empty lines
    consecutive_groups = []
    if link_only_indices:
        curr_pos = non_empty_indices.index(link_only_indices[0])
        current_group = [link_only_indices[0]]
        
        for idx in link_only_indices[1:]:
            pos = non_empty_indices.index(idx)
            if pos == curr_pos + 1:
                current_group.append(idx)
            else:
                consecutive_groups.append(current_group)
                current_group = [idx]
            curr_pos = pos
        consecutive_groups.append(current_group)
        
    lines_to_delete = set()
    list_line_indices = set()
    
    for group in consecutive_groups:
        if len(group) >= 2:
            for idx in group:
                list_line_indices.add(idx)
            # Mark empty lines between items in the group for deletion
            for k in range(group[0], group[-1]):
                if lines[k].strip() == '':
                    lines_to_delete.add(k)
                    
    cleaned_lines = []
    for i, line in enumerate(lines):
        if i in lines_to_delete:
            continue
        if i in list_line_indices:
            stripped = line.strip()
            if not re.match(r'^[\*\-\+\d\.]', stripped):
                indent = len(line) - len(line.lstrip())
                cleaned_lines.append(' ' * indent + '* ' + line.lstrip())
                continue
        cleaned_lines.append(line)
        
    return '\n'.join(cleaned_lines)


def fix_formatting_punctuation(md: str) -> str:
    """Fix spaces around bold text, italics, links, and clean up punctuation placement."""
    # 1. Move punctuation from the end of formatting to the outside (excluding ! and ? to preserve titles/exclamations)
    md = re.sub(r'(\*\*|\*|__|_)([^\*\n]+?)([.,;:]+)\1', r'\1\2\1\3', md)
    md = re.sub(r'\[([^\]\n]+?)([.,;:])\]\(([^\)]+?)\)', r'[\1](\3)\2', md)
    
    # 2. Collapse duplicate punctuation but keep ellipsis
    md = md.replace('...', '…')
    md = re.sub(r'([.,!?;:])\1+', r'\1', md)
    md = md.replace('…', '...')
    
    # 3. Fix spacing around punctuation and formatting/links
    def fix_punctuation_spacing(match):
        word, punc, bracket_or_star = match.groups()
        if punc == '!' and bracket_or_star == '[':
            return f"{word} {punc}{bracket_or_star}"
        return f"{word}{punc} {bracket_or_star}"
    md = re.sub(r'(\b\w+(?:\*\*|\*|__|_)?)([.,!?;:])(\[|\*\*|\*)', fix_punctuation_spacing, md)
    
    # 4. Fix sticking bold/italic/links to adjacent words using strict boundaries
    md = re.sub(r'([a-zA-Zа-яА-ЯёЁ0-9])(?<!\*)\*\*([^\*\n]+?)(?<!\*)\*\*(?!\*)', r'\1 **\2**', md)
    md = re.sub(r'(?<!\*)\*\*([^\*\n]+?)(?<!\*)\*\*([a-zA-Zа-яА-ЯёЁ0-9])', r'**\1** \2', md)
    md = re.sub(r'([a-zA-Zа-яА-ЯёЁ0-9])(?<!\*)\*([^\*\n]+?)(?<!\*)\*(?!\*)', r'\1 *\2*', md)
    md = re.sub(r'(?<!\*)\*([^\*\n]+?)(?<!\*)\*([a-zA-Zа-яА-ЯёЁ0-9])', r'*\1* \2', md)
    
    # Links sticking to words
    md = re.sub(r'(\]\]*\([^\)]+?\))([a-zA-Zа-яА-ЯёЁ0-9])', r'\1 \2', md)
    md = re.sub(r'([a-zA-Zа-яА-ЯёЁ0-9])(\[([^\]]+?)\]\([^\)]+?\))', r'\1 \2', md)
    
    # Inline code sticking to words
    md = re.sub(r'(`[^`\n]+?`)([a-zA-Zа-яА-ЯёЁ0-9])', r'\1 \2', md)
    md = re.sub(r'([a-zA-Zа-яА-ЯёЁ0-9])(`[^`\n]+?`)', r'\1 \2', md)
    
    # 5. Fix sticking formatting/links to each other
    md = re.sub(r'(?<!\*)\*\*([^\*\n]+?)(?<!\*)\*\*(\[)', r'**\1** \2', md)
    md = re.sub(r'(\]\([^\)]+?\))(?<!\*)\*\*([^\*\n]+?)(?<!\*)\*\*(?!\*)', r'\1 **\2**', md)
    md = re.sub(r'(?<!\*)\*([^\*\n]+?)(?<!\*)\*(\[)', r'*\1* \2', md)
    md = re.sub(r'(\]\([^\)]+?\))(?<!\*)\*([^\*\n]+?)(?<!\*)\*(?!\*)', r'\1 *\2*', md)
    md = re.sub(r'(?<!\*)\*\*([^\*\n]+?)(?<!\*)\*\*(?<!\*)\*\*([^\*\n]+?)(?<!\*)\*\*(?!\*)', r'**\1** **\2**', md)
    md = re.sub(r'(\]\([^\)]+?\))(\[)', r'\1 \2', md)
    
    return md



def strip_navigation_junk(md: str) -> str:
    """Remove standalone navigation arrows, buttons, and UI controls."""
    lines = md.split('\n')
    cleaned_lines = []
    
    junk_symbol_pattern = re.compile(r'^\[?[→←▲▼⇒⇐≫≪•«»<>–—\s]+\]?$')
    junk_text_pattern = re.compile(
        r'^\[?(Далее|Назад|Читать далее|Дальше|Next|Prev|Previous|Back|Read more|Share|Поделиться|vk|vkontakte|twitter|facebook|telegram|instagram|linkedin|odnoklassniki)\]?$',
        re.IGNORECASE
    )
    
    for line in lines:
        stripped = line.strip()
        if not stripped:
            cleaned_lines.append(line)
            continue
            
        # Do not strip blockquote markers
        if stripped.startswith('>'):
            cleaned_lines.append(line)
            continue
            
        if re.match(r'^(?:-{3,}|\*{3,}|_{3,})$', stripped):
            cleaned_lines.append(line)
            continue
            
        if junk_symbol_pattern.match(stripped) or junk_text_pattern.match(stripped):
            continue
            
        cleaned_lines.append(line)
        
    return '\n'.join(cleaned_lines)


def normalize_blockquotes(md: str) -> str:
    """Ensure blank lines within blockquotes are formatted with '>' to keep quote continuity."""
    lines = md.split('\n')
    for i in range(len(lines)):
        if lines[i].strip() == '':
            prev_idx = i - 1
            while prev_idx >= 0 and lines[prev_idx].strip() == '':
                prev_idx -= 1
            next_idx = i + 1
            while next_idx < len(lines) and lines[next_idx].strip() == '':
                next_idx += 1
                
            if (prev_idx >= 0 and lines[prev_idx].strip().startswith('>')) and \
               (next_idx < len(lines) and lines[next_idx].strip().startswith('>')):
                lines[i] = '>'
    return '\n'.join(lines)


def fix_header_spacing(md: str) -> str:
    """Ensure there is a single blank line before and after headers."""
    lines = md.split('\n')
    cleaned = []
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith('#') and re.match(r'^#+\s+', stripped):
            if cleaned and cleaned[-1].strip() != '' and not cleaned[-1].strip().startswith('#'):
                cleaned.append('')
            cleaned.append(line)
        else:
            if cleaned and cleaned[-1].strip().startswith('#') and stripped != '' and not stripped.startswith('#'):
                cleaned.append('')
            cleaned.append(line)
    return '\n'.join(cleaned)


def custom_rstrip(line: str) -> str:
    """Strip trailing whitespace but preserve standard markdown breaks (exactly two spaces)."""
    if line.endswith('  '):
        return line[:-2].rstrip() + '  '
    return line.rstrip()


def clean_markdown(markdown_content: str) -> str:
    """
    Post-process the generated Markdown content to strip trailing whitespaces,
    clean punctuation spacing, normalize parentheses/brackets/em-dashes,
    remove excessive newlines, and ensure a single trailing newline.
    Also fixes split links, decodes URLs, fixes lists and headers.
    """
    content = markdown_content.replace('\xa0', ' ').replace('\u200b', '')
    
    parts = re.split(r'(```[\s\S]*?```)', content)
    processed_parts = []
    
    for part in parts:
        if part.startswith('```'):
            processed_parts.append(part)
        else:
            lines = [custom_rstrip(line) for line in part.split('\n')]
            cleaned_lines = []
            for line in lines:
                indent = len(line) - len(line.lstrip())
                text_part = line.lstrip()
                
                text_part = re.sub(r'[ \t]+', ' ', text_part)
                text_part = re.sub(r'\s+([.,!?;])', r'\1', text_part)
                text_part = re.sub(r'\(\s+(.*?)\s+\)', r'(\1)', text_part)
                text_part = re.sub(r'\[\s+(.*?)\s+\]', r'[\1]', text_part)
                text_part = re.sub(r'\s+(--?)\s+', ' — ', text_part)
                text_part = re.sub(r'\s+—\s+', ' — ', text_part)
                
                cleaned_lines.append(' ' * indent + text_part)
                
            text = '\n'.join(cleaned_lines)
            
            text = collapse_links(text)
            text = strip_navigation_junk(text)
            text = fix_lists_and_links(text)
            text = fix_formatting_punctuation(text)
            text = normalize_blockquotes(text)
            text = fix_header_spacing(text)
            
            processed_parts.append(text)
            
    content = ''.join(processed_parts)
    content = re.sub(r'\n{3,}', '\n\n', content)
    content = content.strip('\n')
    
    content = fix_markdown_image_syntax(content)
    
    return content + '\n'



class MarkdownConverter(BaseConverter):
    """
    Converts HTML content to Markdown and embeds images as base64 reference links.
    """
    
    @property
    def file_extension(self) -> str:
        return ".md"

    async def convert(self, html_content: str, base_url: str, article_slug: str) -> str:
        # 0. Preprocess HTML
        html_content = preprocess_html(html_content)
        
        # 0.5 Preprocess math formulas to standard Markdown delimiters
        soup_formulas = BeautifulSoup(html_content, "lxml")
        preprocess_formulas(soup_formulas, to_markdown=True)
        html_content = str(soup_formulas)
        
        # 1. Download/resolve images and rewrite URLs in HTML to reference IDs
        logger.info(f"Embedding images as reference base64 for article: {article_slug}")
        html_with_refs, image_refs = await self._prepare_base64_references(html_content, base_url)
        
        # 2. Convert HTML to Markdown using custom converter
        logger.info("Converting HTML to Markdown")
        converter = CustomMarkdownConverter(
            heading_style="ATX",
            code_language_callback=lambda el: el.get("class", [""])[0].replace("language-", "") if el.get("class") else ""
        )
        markdown_content = converter.convert(html_with_refs)
        
        # 3. Clean up formatting and extra newlines
        markdown_content = clean_markdown(markdown_content)
        
        # 4. Post-process to replace inline references with reference-style links
        # Replace `![alt](img_ref_N)` with `![alt][img_ref_N]`
        for ref_id in image_refs.keys():
            markdown_content = re.sub(
                rf'!\[(.*?)\]\({ref_id}\)',
                rf'![\1][{ref_id}]',
                markdown_content
            )
            
        # 5. Append references at the end
        if image_refs:
            markdown_content += "\n\n"
            for ref_id, b64_uri in image_refs.items():
                markdown_content += f"[{ref_id}]: {b64_uri}\n"
                
        # 6. Apply validate_and_normalize_math to standard delimiters
        markdown_content = validate_and_normalize_math(markdown_content)
                
        return markdown_content

    async def _prepare_base64_references(self, html_content: str, base_url: str) -> Tuple[str, Dict[str, str]]:
        """
        Finds all image tags, downloads/resolves the images concurrently, and converts them to base64 URIs.
        Replaces the img src in HTML with a reference ID (e.g. img_ref_1).
        Supports web URLs, local file paths, and base64 URIs (which are decoded, optimized, and re-encoded).
        Returns the modified HTML and a dictionary mapping reference ID -> base64 URI.
        """
        soup = BeautifulSoup(html_content, "lxml")
        img_tags = soup.find_all("img")
        
        if not img_tags:
            return str(soup), {}

        download_tasks = []
        img_rewrites = []
        semaphore = asyncio.Semaphore(3)
        image_refs = {}
        
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
            for idx, img in enumerate(img_tags):
                src = extract_real_image_src(img)
                if not src:
                    continue
                
                ref_id = f"img_ref_{idx}"
                img["src"] = ref_id
                
                # Infer image role
                role = infer_image_role_from_tag(img)
                
                # A. Base64 encoded images
                if src.startswith("data:image/"):
                    try:
                        src_clean = re.sub(r'\s+', '', src)
                        pattern = re.compile(r'^data:image/([^;]+);base64,(.*)$')
                        match = pattern.match(src_clean)
                        if match:
                            ext = match.group(1)
                            img_bytes = base64.b64decode(match.group(2))
                            mime = f"image/{ext}"
                            
                            # Optimize the decoded base64 image!
                            optimized = self._optimize_image(img_bytes, mime, max_dim=1200, quality=75, role=role)
                            if optimized:
                                opt_bytes, opt_mime = optimized
                                opt_b64 = base64.b64encode(opt_bytes).decode("utf-8")
                                image_refs[ref_id] = f"data:{opt_mime};base64,{opt_b64}"
                            else:
                                image_refs[ref_id] = src
                    except Exception as e:
                        logger.error(f"Error parsing inline base64 image in MD: {e}")
                        image_refs[ref_id] = src
                    continue
                
                # B. Web URL or local file path
                is_web_url = src.startswith(("http://", "https://")) or base_url.startswith(("http://", "https://"))
                
                if is_web_url:
                    # Resolve relative URLs
                    absolute_url = urljoin(base_url, src)
                    task = self._download_single_image_bytes(client, absolute_url, semaphore, base_url, role=role)
                    download_tasks.append(task)
                    img_rewrites.append((ref_id, task, absolute_url))
                else:
                    # It's a local file path
                    try:
                        if base_url.startswith("file://"):
                            base_path = Path(urlparse(base_url).path)
                        else:
                            base_path = Path(base_url)
                            
                        if base_path.is_file():
                            base_dir = base_path.parent
                        else:
                            base_dir = base_path
                            
                        if src.startswith("file://"):
                            img_path = Path(urlparse(src).path)
                        else:
                            img_path = Path(src)
                            
                        if not img_path.is_absolute():
                            img_path = (base_dir / img_path).resolve()
                            
                        if img_path.exists() and img_path.is_file():
                            img_bytes = img_path.read_bytes()
                            mime_type, _ = mimetypes.guess_type(str(img_path))
                            if not mime_type:
                                mime_type = "image/jpeg"
                                
                            # Optimize the local image!
                            optimized = self._optimize_image(img_bytes, mime_type, max_dim=1200, quality=75, role=role)
                            if optimized:
                                opt_bytes, opt_mime = optimized
                                opt_b64 = base64.b64encode(opt_bytes).decode("utf-8")
                                image_refs[ref_id] = f"data:{opt_mime};base64,{opt_b64}"
                            else:
                                opt_b64 = base64.b64encode(img_bytes).decode("utf-8")
                                image_refs[ref_id] = f"data:{mime_type};base64,{opt_b64}"
                        else:
                            logger.warning(f"Local image path does not exist for MD: {img_path}")
                            image_refs[ref_id] = src
                    except Exception as e:
                        logger.error(f"Error loading local image {src} for MD: {e}")
                        image_refs[ref_id] = src
            
            # Execute downloads concurrently
            if download_tasks:
                results = await asyncio.gather(*download_tasks, return_exceptions=True)
                
                # Map results to reference IDs
                for ref_id, task, absolute_url in img_rewrites:
                    idx_task = download_tasks.index(task)
                    result = results[idx_task]
                    
                    if isinstance(result, Exception) or not result:
                        logger.warning(f"Failed to download image for reference {ref_id} ({absolute_url}): {result}")
                        # Fallback to the absolute URL
                        image_refs[ref_id] = absolute_url
                    else:
                        img_bytes, mime_type = result
                        b64_str = base64.b64encode(img_bytes).decode("utf-8")
                        image_refs[ref_id] = f"data:{mime_type};base64,{b64_str}"
                        
        return str(soup), image_refs



class HTMLConverter(BaseConverter):
    """
    Converts HTML content to cleaned, self-contained HTML (using base64-embedded images)
    styled with a premium, responsive reading layout and light/dark theme toggle.
    """

    @property
    def file_extension(self) -> str:
        return ".html"

    async def convert(self, html_content: str, base_url: str, article_slug: str) -> str:
        # 1. Preprocess HTML
        cleaned_html = preprocess_html(html_content)

        # 2. Download and embed images as base64
        logger.info(f"Embedding images as base64 for article: {article_slug}")
        html_with_b64_images = await self._embed_images_as_base64(cleaned_html, base_url)

        # 3. Construct premium self-contained HTML document
        soup = BeautifulSoup(html_with_b64_images, "lxml")
        
        # Identify illustration vs inline images
        for img in soup.find_all("img"):
            is_inline = False
            for sib in img.previous_siblings:
                if sib.name is None:
                    if str(sib).strip():
                        is_inline = True
                        break
                elif sib.name in {"a", "span", "b", "strong", "i", "em", "code", "del", "strike", "s"}:
                    is_inline = True
                    break
                else:
                    break
            if not is_inline:
                for sib in img.next_siblings:
                    if sib.name is None:
                        if str(sib).strip():
                            is_inline = True
                            break
                    elif sib.name in {"a", "span", "b", "strong", "i", "em", "code", "del", "strike", "s"}:
                        is_inline = True
                        break
                    else:
                        break
            
            # Check for explicitly small dimensions
            w = img.get("width")
            h = img.get("height")
            try:
                if (w and int(w) <= 40) or (h and int(h) <= 40):
                    is_inline = True
            except ValueError:
                pass
                
            if not is_inline:
                classes = img.get("class", [])
                if isinstance(classes, str):
                    classes = [classes]
                img["class"] = classes + ["illustration"]

        # Wrap all tables in a responsive table-container div
        for table in soup.find_all("table"):
            parent = table.parent
            if not (parent and parent.name == "div" and parent.get("class") == ["table-container"]):
                table.wrap(soup.new_tag("div", attrs={"class": "table-container"}))

        # Extract title from h1 if available
        h1_tag = soup.find("h1")
        title_text = h1_tag.get_text() if h1_tag else "Сохраненная статья"

        # Get body children/content
        if soup.body:
            body_content = "".join(str(child) for child in soup.body.children)
        else:
            body_content = str(soup)

        return self._wrap_in_template(title_text, body_content)

    async def _embed_images_as_base64(self, html_content: str, base_url: str) -> str:
        """
        Find all img tags, download their content, and replace src with base64 data URI.
        """
        soup = BeautifulSoup(html_content, "lxml")
        img_tags = soup.find_all("img")
        
        if not img_tags:
            return str(soup)

        download_tasks = []
        img_rewrites = []
        semaphore = asyncio.Semaphore(3)

        async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
            for img in img_tags:
                src = extract_real_image_src(img)
                if not src:
                    continue

                # Keep base64 as is
                if src.startswith("data:image/"):
                    img["src"] = src
                    continue

                role = infer_image_role_from_tag(img)
                absolute_url = urljoin(base_url, src)
                task = self._download_single_image_bytes(client, absolute_url, semaphore, base_url, role=role)
                download_tasks.append(task)
                img_rewrites.append((img, task, absolute_url))

            if download_tasks:
                results = await asyncio.gather(*download_tasks, return_exceptions=True)
                for img, task, absolute_url in img_rewrites:
                    idx = download_tasks.index(task)
                    result = results[idx]

                    if isinstance(result, Exception) or not result:
                        logger.warning(f"Failed to embed image {absolute_url}: {result}")
                        img["src"] = absolute_url  # fallback to original URL
                    else:
                        img_bytes, mime_type = result
                        b64_str = base64.b64encode(img_bytes).decode("utf-8")
                        img["src"] = f"data:{mime_type};base64,{b64_str}"

        return str(soup)

    def _wrap_in_template(self, title: str, body_content: str) -> str:
        """Wrap body content in a styled premium HTML document with Dark/Light theme."""
        return f"""<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{title}</title>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800&display=swap" rel="stylesheet">
    <style>
        *, *::before, *::after {{
            box-sizing: border-box;
        }}

        :root {{
            --bg-color: #0f172a;
            --text-color: #f1f5f9;
            --title-color: #ffffff;
            --primary-color: #38bdf8;
            --secondary-color: #94a3b8;
            --card-bg: #1e293b;
            --border-color: #334155;
            --quote-bg: #1e293b;
            --quote-border: #38bdf8;
            --code-bg: #1e293b;
            --code-text: #f472b6;
            --max-width: 720px;
        }}

        :root.light {{
            --bg-color: #f8fafc;
            --text-color: #334155;
            --title-color: #0f172a;
            --primary-color: #0284c7;
            --secondary-color: #64748b;
            --card-bg: #f1f5f9;
            --border-color: #e2e8f0;
            --quote-bg: #f1f5f9;
            --quote-border: #0284c7;
            --code-bg: #f1f5f9;
            --code-text: #db2777;
        }}

        body {{
            background-color: var(--bg-color);
            color: var(--text-color);
            font-family: 'Inter', -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            line-height: 1.8;
            font-size: 1.125rem;
            margin: 0;
            padding: 3rem 1.5rem;
            display: flex;
            justify-content: center;
            transition: background-color 0.3s ease, color 0.3s ease;
            word-wrap: break-word;
            overflow-wrap: break-word;
            -webkit-font-smoothing: antialiased;
            -moz-osx-font-smoothing: grayscale;
        }}

        article {{
            max-width: var(--max-width);
            width: 100%;
        }}

        h1, h2, h3, h4, h5, h6 {{
            color: var(--title-color);
            font-weight: 800;
            line-height: 1.3;
            margin-top: 2.5rem;
            margin-bottom: 1rem;
            letter-spacing: -0.025em;
            transition: color 0.3s ease;
            overflow-wrap: break-word;
            word-break: break-word;
        }}

        h1 {{
            font-size: 2.5rem;
            border-bottom: 1px solid var(--border-color);
            padding-bottom: 1rem;
            margin-top: 1rem;
        }}

        h2 {{
            font-size: 1.875rem;
            border-bottom: 1px solid var(--border-color);
            padding-bottom: 0.5rem;
        }}

        h3 {{
            font-size: 1.5rem;
        }}

        p {{
            margin-top: 0;
            margin-bottom: 1.5rem;
        }}

        a {{
            color: var(--primary-color);
            text-decoration: none;
            border-bottom: 1px dashed var(--primary-color);
            transition: all 0.2s ease;
        }}

        a:hover {{
            color: var(--title-color);
            border-bottom-style: solid;
        }}

        img {{
            max-width: 100%;
            height: auto;
            vertical-align: middle;
        }}

        img.illustration {{
            display: block;
            margin: 2rem auto;
            border-radius: 12px;
            box-shadow: 0 10px 25px -5px rgba(0, 0, 0, 0.2);
            border: 1px solid var(--border-color);
            transition: border-color 0.3s ease;
        }}

        blockquote {{
            margin: 2rem 0;
            padding: 1rem 1.5rem;
            background-color: var(--quote-bg);
            border-left: 4px solid var(--quote-border);
            border-radius: 0 8px 8px 0;
            font-style: italic;
            color: var(--text-color);
            transition: background-color 0.3s ease, border-left-color 0.3s ease;
        }}
        
        blockquote p {{
            margin-bottom: 0;
        }}

        code {{
            font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
            font-size: 0.9em;
            background-color: var(--code-bg);
            color: var(--code-text);
            padding: 0.2em 0.4em;
            border-radius: 6px;
            border: 1px solid var(--border-color);
            transition: background-color 0.3s ease, border-color 0.3s ease, color 0.3s ease;
            word-wrap: break-word;
            overflow-wrap: break-word;
        }}

        pre {{
            background-color: var(--card-bg);
            padding: 1.25rem;
            border-radius: 12px;
            overflow-x: auto;
            border: 1px solid var(--border-color);
            margin: 2rem 0;
            transition: background-color 0.3s ease, border-color 0.3s ease;
        }}

        pre code {{
            background-color: transparent;
            color: var(--text-color);
            padding: 0;
            border: none;
            font-size: 0.95rem;
            word-wrap: normal;
            overflow-wrap: normal;
        }}

        ul, ol {{
            margin-top: 0;
            margin-bottom: 1.5rem;
            padding-left: 1.5rem;
        }}

        li {{
            margin-bottom: 0.5rem;
        }}

        hr {{
            border: 0;
            height: 1px;
            background: linear-gradient(to right, transparent, var(--border-color), transparent);
            margin: 3rem 0;
        }}

        .table-container {{
            width: 100%;
            overflow-x: auto;
            margin: 2rem 0;
            border: 1px solid var(--border-color);
            border-radius: 12px;
            background-color: var(--card-bg);
            transition: border-color 0.3s ease, background-color 0.3s ease;
            -webkit-overflow-scrolling: touch;
        }}

        table {{
            width: 100%;
            border-collapse: collapse;
            font-size: 0.95rem;
            text-align: left;
        }}

        th, td {{
            padding: 0.75rem 1rem;
            border-bottom: 1px solid var(--border-color);
            color: var(--text-color);
            transition: border-color 0.3s ease, color 0.3s ease;
        }}

        th {{
            background-color: var(--card-bg);
            font-weight: 600;
            color: var(--title-color);
        }}

        tr:last-child td {{
            border-bottom: none;
        }}
        
        .article-footer {{
            margin-top: 4rem;
            padding-top: 2rem;
            border-top: 1px solid var(--border-color);
            font-size: 0.875rem;
            color: var(--secondary-color);
            text-align: center;
            transition: border-top-color 0.3s ease, color 0.3s ease;
        }}

        #theme-toggle {{
            position: fixed;
            bottom: 2rem;
            right: 2rem;
            width: 3.5rem;
            height: 3.5rem;
            border-radius: 50%;
            background-color: var(--card-bg);
            border: 1px solid var(--border-color);
            color: var(--text-color);
            cursor: pointer;
            display: flex;
            align-items: center;
            justify-content: center;
            box-shadow: 0 10px 15px -3px rgba(0, 0, 0, 0.3);
            transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
            z-index: 100;
        }}
        
        #theme-toggle:hover {{
            transform: scale(1.1);
            background-color: var(--border-color);
        }}
        
        #theme-toggle svg {{
            width: 1.5rem;
            height: 1.5rem;
            fill: none;
            stroke: currentColor;
            stroke-width: 2;
            stroke-linecap: round;
            stroke-linejoin: round;
        }}

        @media (max-width: 640px) {{
            body {{
                padding: 1.5rem 1rem;
                font-size: 1rem;
            }}
            
            h1 {{
                font-size: 1.8rem;
                margin-top: 1rem;
                padding-bottom: 0.75rem;
            }}
            
            h2 {{
                font-size: 1.4rem;
                margin-top: 2rem;
            }}
            
            h3 {{
                font-size: 1.2rem;
                margin-top: 1.75rem;
            }}
            
            pre {{
                padding: 1rem;
                margin: 1.5rem 0;
                border-radius: 8px;
            }}
            
            pre code {{
                font-size: 0.85rem;
            }}
            
            blockquote {{
                margin: 1.5rem 0;
                padding: 0.75rem 1rem;
            }}
            
            img.illustration {{
                margin: 1.5rem auto;
                border-radius: 8px;
            }}
            
            .table-container {{
                margin: 1.5rem 0;
                border-radius: 8px;
            }}
            
            th, td {{
                padding: 0.5rem 0.75rem;
                font-size: 0.85rem;
            }}
            
            #theme-toggle {{
                bottom: 1.5rem;
                right: 1.5rem;
                width: 3rem;
                height: 3rem;
            }}
        }}
    </style>
</head>
<body>
    <article>
        {body_content}
        <div class="article-footer">
            Сохранено с помощью Read It Later Bot
        </div>
    </article>

    <button id="theme-toggle" aria-label="Toggle theme">
        <svg id="theme-icon" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">
            <!-- Default sun icon -->
            <circle cx="12" cy="12" r="4" />
            <path d="M3 12h1m8-9v1m8 8h1m-9 8v1M5.6 5.6l.7.7m11.4-.7l-.7.7m0 11.4l.7.7m-11.4-.7l-.7.7" />
        </svg>
    </button>
    <script>
        const toggle = document.getElementById('theme-toggle');
        const icon = document.getElementById('theme-icon');
        
        const sunIcon = `<circle cx="12" cy="12" r="4" /><path d="M3 12h1m8-9v1m8 8h1m-9 8v1M5.6 5.6l.7.7m11.4-.7l-.7.7m0 11.4l.7.7m-11.4-.7l-.7.7" />`;
        const moonIcon = `<path d="M12 3c.132 0 .263 0 .393 0a7.5 7.5 0 0 0 7.92 12.446a9 9 0 1 1 -8.313 -12.454z" />`;
        
        const savedTheme = localStorage.getItem('theme');
        const prefersLight = window.matchMedia('(prefers-color-scheme: light)').matches;
        
        if (savedTheme === 'light' || (!savedTheme && prefersLight)) {{
            document.documentElement.classList.add('light');
            icon.innerHTML = moonIcon;
        }} else {{
            icon.innerHTML = sunIcon;
        }}
        
        toggle.addEventListener('click', () => {{
            const isLight = document.documentElement.classList.toggle('light');
            localStorage.setItem('theme', isLight ? 'light' : 'dark');
            icon.innerHTML = isLight ? moonIcon : sunIcon;
        }});
    </script>
</body>
</html>
"""


def _xml_escape_text(text: str) -> str:
    """Escape plain text for safe inclusion in XHTML/XML text nodes and attributes."""
    return html.escape(text, quote=False)


def detect_language(text: str) -> str:
    """
    Heuristic language detection for EPUB metadata.
    Cyrillic letter ratio above threshold -> ru, otherwise en.
    """
    if config.EPUB_LANGUAGE:
        return config.EPUB_LANGUAGE
    if not text:
        return "en"
    cyrillic = len(re.findall(r"[\u0400-\u04FF]", text))
    latin = len(re.findall(r"[A-Za-z]", text))
    letters = cyrillic + latin
    if letters == 0:
        return "en"
    if cyrillic / letters >= 0.15:
        return "ru"
    return "en"


def _serialize_xhtml_children(element) -> str:
    """Serialize element children as an XHTML fragment with XML-safe escaping."""
    from bs4 import NavigableString, Comment

    parts = []
    for child in element.children:
        if isinstance(child, Comment):
            continue
        if isinstance(child, NavigableString):
            parts.append(_xml_escape_text(str(child)))
        else:
            parts.append(child.decode(formatter="minimal"))
    return "".join(parts)


def _normalize_split_tag(split_at: str) -> str:
    """Normalize chapter split tag to a supported heading level."""
    split_at = (split_at or "h1").lower()
    return split_at if split_at in ("h1", "h2") else "h1"


def _find_headings_in_range(start_heading, end_heading, tag: str) -> List:
    """Return headings of ``tag`` that appear after ``start_heading`` and before ``end_heading``."""
    results = []
    current = start_heading.next_element
    while current and current is not end_heading:
        if getattr(current, "name", None) == tag:
            results.append(current)
        current = current.next_element
    return results


def _assign_stable_heading_ids(soup: BeautifulSoup, toc_max_depth: int = 2) -> None:
    """Assign stable id attributes to headings included in the EPUB table of contents."""
    body = soup.body if soup.body else soup
    toc_max_depth = max(1, min(int(toc_max_depth), 6))

    h1_tags = body.find_all("h1")
    for h1_index, h1 in enumerate(h1_tags, start=1):
        if not h1.get("id"):
            h1["id"] = f"ch-{h1_index}"

        if toc_max_depth < 2:
            continue

        next_h1 = h1_tags[h1_index] if h1_index < len(h1_tags) else None
        for h2_index, h2 in enumerate(_find_headings_in_range(h1, next_h1, "h2"), start=1):
            if not h2.get("id"):
                h2["id"] = f"ch-{h1_index}-sec-{h2_index}"

            if toc_max_depth < 3:
                continue

            next_h2 = h2.find_all_next("h2")
            next_h2 = next_h2[0] if next_h2 else None
            if next_h2 and next_h1:
                # Stop at whichever comes first in document order
                if next_h2.find_next(lambda el: el is next_h1):
                    boundary = next_h1
                else:
                    boundary = next_h2
            elif next_h2:
                boundary = next_h2
            else:
                boundary = next_h1

            for h3_index, h3 in enumerate(_find_headings_in_range(h2, boundary, "h3"), start=1):
                if not h3.get("id"):
                    h3["id"] = f"ch-{h1_index}-sec-{h2_index}-{h3_index}"


def _collect_nodes_until_next_split(heading, split_tag: str) -> List:
    """Collect a heading and following siblings until the next split-level heading."""
    nodes = [heading]
    current = heading
    while True:
        next_sibling = current.next_sibling
        if next_sibling is None:
            break
        if getattr(next_sibling, "name", None) == split_tag:
            break
        nodes.append(next_sibling)
        current = next_sibling
    return nodes


def split_html_into_chapters(
    soup: BeautifulSoup,
    split_at: str = "h1",
    toc_max_depth: int = 2,
    assign_ids: bool = True,
) -> List[Dict[str, Any]]:
    """
    Split HTML soup into chapters at ``split_at`` headings.

    Returns a list of chapter dicts:
    ``{title, anchor_id, html_fragment, level}``.
    """
    split_tag = _normalize_split_tag(split_at)
    split_level = int(split_tag[1])
    body = soup.body if soup.body else soup

    if assign_ids:
        _assign_stable_heading_ids(soup, toc_max_depth=toc_max_depth)

    split_headings = body.find_all(split_tag)
    if len(split_headings) <= 1:
        if split_headings:
            title = split_headings[0].get_text(strip=True) or "Saved Article"
            anchor_id = split_headings[0].get("id", "ch-1")
            level = split_level
        else:
            title = "Saved Article"
            anchor_id = "ch-1"
            level = 1

        return [{
            "title": title,
            "anchor_id": anchor_id,
            "html_fragment": _serialize_xhtml_children(body),
            "level": level,
        }]

    chapters = []
    for heading in split_headings:
        fragment_soup = BeautifulSoup("<body></body>", "lxml")
        fragment_body = fragment_soup.body

        nodes_to_move = _collect_nodes_until_next_split(heading, split_tag)
        if heading is split_headings[0]:
            preamble = []
            previous = heading.previous_sibling
            while previous is not None:
                if getattr(previous, "name", None) == split_tag:
                    break
                preamble.insert(0, previous)
                previous = previous.previous_sibling
            nodes_to_move = preamble + nodes_to_move

        for node in nodes_to_move:
            fragment_body.append(node.extract())

        chapters.append({
            "title": heading.get_text(strip=True) or "Saved Article",
            "anchor_id": heading.get("id", f"ch-{len(chapters) + 1}"),
            "html_fragment": _serialize_xhtml_children(fragment_body),
            "level": split_level,
        })

    return chapters


def _build_ncx_nav_points(
    soup: BeautifulSoup,
    fallback_title: str,
    toc_max_depth: int,
    multi_chapter: bool,
) -> Tuple[str, int]:
    """Build hierarchical NCX navPoint XML and return it with total navPoint count."""
    body = soup.body if soup.body else soup
    toc_max_depth = max(1, min(int(toc_max_depth), 6))

    play_order = 0
    nav_point_count = 0

    def render_nav_point(nav_id: str, title: str, src: str, children_xml: str = "") -> str:
        nonlocal play_order, nav_point_count
        play_order += 1
        nav_point_count += 1
        escaped_title = _xml_escape_text(title)
        return (
            f'    <navPoint id="{nav_id}" playOrder="{play_order}">\n'
            f'      <navLabel>\n'
            f'        <text>{escaped_title}</text>\n'
            f'      </navLabel>\n'
            f'      <content src="{src}"/>\n'
            f'{children_xml}'
            f'    </navPoint>\n'
        )

    nav_map_parts = []
    h1_tags = body.find_all("h1")

    if not h1_tags:
        src = "article.xhtml"
        nav_map_parts.append(render_nav_point("navpoint-1", fallback_title, src))
        return "".join(nav_map_parts), nav_point_count

    for h1_index, h1 in enumerate(h1_tags, start=1):
        if multi_chapter:
            src = f"chapter-{h1_index:02d}.xhtml#{h1.get('id', f'ch-{h1_index}')}"
        else:
            anchor = h1.get("id")
            src = f"article.xhtml#{anchor}" if anchor else "article.xhtml"

        children_xml = ""
        if toc_max_depth >= 2:
            next_h1 = h1_tags[h1_index] if h1_index < len(h1_tags) else None
            child_parts = []
            for h2_index, h2 in enumerate(_find_headings_in_range(h1, next_h1, "h2"), start=1):
                h2_anchor = h2.get("id", f"ch-{h1_index}-sec-{h2_index}")
                if multi_chapter:
                    h2_src = f"chapter-{h1_index:02d}.xhtml#{h2_anchor}"
                else:
                    h2_src = f"article.xhtml#{h2_anchor}"
                child_parts.append(
                    render_nav_point(
                        f"navpoint-h1-{h1_index}-h2-{h2_index}",
                        h2.get_text(strip=True) or f"Section {h2_index}",
                        h2_src,
                    )
                )
            children_xml = "".join(child_parts)

        nav_map_parts.append(
            render_nav_point(
                f"navpoint-h1-{h1_index}",
                h1.get_text(strip=True) or f"Chapter {h1_index}",
                src,
                children_xml=children_xml,
            )
        )

    return "".join(nav_map_parts), nav_point_count


def validate_epub_structure(epub_bytes: bytes) -> list[str]:
    """
    Validate structural requirements of an in-memory EPUB archive.

    Returns a list of warning strings; an empty list means the EPUB passed
    all checks.
    """
    warnings: list[str] = []

    try:
        with zipfile.ZipFile(io.BytesIO(epub_bytes), "r") as epub:
            namelist = epub.namelist()

            if not namelist:
                warnings.append("EPUB archive is empty")
                return warnings

            if namelist[0] != "mimetype":
                warnings.append("mimetype must be the first ZIP entry")

            if "mimetype" in namelist:
                mimetype_info = epub.getinfo("mimetype")
                if mimetype_info.compress_type != zipfile.ZIP_STORED:
                    warnings.append("mimetype entry must use ZIP_STORED compression")
                try:
                    mimetype_body = epub.read("mimetype").decode("utf-8").strip()
                except Exception as exc:
                    warnings.append(f"failed to read mimetype: {exc}")
                else:
                    if mimetype_body != "application/epub+zip":
                        warnings.append(
                            'mimetype content must be exactly "application/epub+zip"'
                        )
            else:
                warnings.append("missing required entry: mimetype")

            required_entries = (
                "META-INF/container.xml",
                "OEBPS/content.opf",
                "OEBPS/toc.ncx",
                "OEBPS/style.css",
            )
            for entry in required_entries:
                if entry not in namelist:
                    warnings.append(f"missing required entry: {entry}")

            xhtml_entries = [
                name for name in namelist
                if name.startswith("OEBPS/") and name.endswith(".xhtml")
            ]
            if not xhtml_entries:
                warnings.append("missing required entry: at least one OEBPS/*.xhtml file")

            xml_entries = ["OEBPS/content.opf", "OEBPS/toc.ncx", *xhtml_entries]
            for entry in xml_entries:
                if entry not in namelist:
                    continue
                try:
                    ET.parse(io.BytesIO(epub.read(entry)))
                except ET.ParseError as exc:
                    warnings.append(f"malformed XML in {entry}: {exc}")
                except Exception as exc:
                    warnings.append(f"failed to parse XML in {entry}: {exc}")

    except zipfile.BadZipFile:
        warnings.append("invalid ZIP archive")
    except Exception as exc:
        warnings.append(f"failed to open EPUB archive: {exc}")

    return warnings


def build_epub_debug_report(epub_bytes: bytes) -> dict[str, Any]:
    """Collect EPUB fidelity stats and validation warnings for debug export."""
    validation_warnings = validate_epub_structure(epub_bytes)
    chapter_count = 0
    image_count = 0
    math_ml_count = 0
    math_fallback_count = 0

    with zipfile.ZipFile(io.BytesIO(epub_bytes), "r") as epub:
        for name in epub.namelist():
            if name.startswith("OEBPS/") and name.endswith(".xhtml"):
                chapter_count += 1
                try:
                    xhtml = epub.read(name).decode("utf-8", errors="replace")
                except Exception:
                    continue
                math_ml_count += xhtml.count("<math")
                math_fallback_count += xhtml.count('class="math-fallback"')
            elif name.startswith("OEBPS/images/") and not name.endswith("/"):
                image_count += 1

    return {
        "chapter_count": chapter_count,
        "image_count": image_count,
        "math_ml_count": math_ml_count,
        "math_fallback_count": math_fallback_count,
        "validation_warnings": validation_warnings,
    }


class EPUBConverter(BaseConverter):
    """
    Converts HTML content to a self-contained EPUB ebook containing downloaded images.
    """
    
    @property
    def file_extension(self) -> str:
        return ".epub"

    async def convert(
        self,
        html_content: str,
        base_url: str,
        article_slug: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> bytes:
        # 1. Preprocess HTML
        html_content = preprocess_html(html_content)
        
        # 1.5 Preprocess math formulas to namespaced MathML
        soup_formulas = BeautifulSoup(html_content, "lxml")
        preprocess_formulas(soup_formulas, to_markdown=False)
        html_content = str(soup_formulas)
        
        # 2. Extract and download all images, mapping them to local EPUB paths
        soup = BeautifulSoup(html_content, "lxml")
        img_tags = soup.find_all("img")
        
        # Identify illustration vs inline images
        for img in img_tags:
            is_inline = False
            for sib in img.previous_siblings:
                if sib.name is None:
                    if str(sib).strip():
                        is_inline = True
                        break
                elif sib.name in {"a", "span", "b", "strong", "i", "em", "code", "del", "strike", "s"}:
                    is_inline = True
                    break
                else:
                    break
            if not is_inline:
                for sib in img.next_siblings:
                    if sib.name is None:
                        if str(sib).strip():
                            is_inline = True
                            break
                    elif sib.name in {"a", "span", "b", "strong", "i", "em", "code", "del", "strike", "s"}:
                        is_inline = True
                        break
                    else:
                        break
            
            # Check for explicitly small dimensions
            w = img.get("width")
            h = img.get("height")
            try:
                if (w and int(w) <= 40) or (h and int(h) <= 40):
                    is_inline = True
            except ValueError:
                pass
                
            if not is_inline:
                classes = img.get("class", [])
                if isinstance(classes, str):
                    classes = [classes]
                img["class"] = classes + ["illustration"]
        
        images_to_pack = []  # list of (epub_path, bytes, mime_type)
        semaphore = asyncio.Semaphore(3)
        
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
            download_tasks = []
            img_rewrites = []
            
            for idx, img in enumerate(img_tags):
                src = extract_real_image_src(img)
                if not src:
                    continue
                
                # Infer image role
                role = infer_image_role_from_tag(img)
                
                # A. Base64 encoded images
                if src.startswith("data:image/"):
                    try:
                        src_clean = re.sub(r'\s+', '', src)
                        pattern = re.compile(r'^data:image/([^;]+);base64,(.*)$')
                        match = pattern.match(src_clean)
                        if match:
                            ext = match.group(1)
                            img_bytes = base64.b64decode(match.group(2))
                            mime = f"image/{ext}"
                            
                            # Optimize the decoded base64 image!
                            optimized = self._optimize_image(img_bytes, mime, max_dim=1200, quality=75, role=role)
                            if optimized:
                                opt_bytes, opt_mime = optimized
                                opt_ext = self._get_extension_from_mime(opt_mime)
                                if not opt_ext:
                                    opt_ext = f".{ext}"
                                epub_img_name = f"img_{idx}{opt_ext}"
                                images_to_pack.append((f"OEBPS/images/{epub_img_name}", opt_bytes, opt_mime))
                                img["src"] = f"images/{epub_img_name}"
                            else:
                                epub_img_name = f"img_{idx}.{ext}"
                                images_to_pack.append((f"OEBPS/images/{epub_img_name}", img_bytes, mime))
                                img["src"] = f"images/{epub_img_name}"
                    except Exception as e:
                        logger.error(f"Error parsing inline base64 image in EPUB: {e}")
                    continue
                
                # B. Web URL or local file path
                is_web_url = src.startswith(("http://", "https://")) or base_url.startswith(("http://", "https://"))
                
                if is_web_url:
                    # Resolve relative URLs
                    absolute_url = urljoin(base_url, src)
                    task = self._download_single_image_bytes(client, absolute_url, semaphore, base_url, role=role)
                    download_tasks.append(task)
                    img_rewrites.append((img, idx, task, absolute_url))
                else:
                    # It's a local file path
                    try:
                        if base_url.startswith("file://"):
                            base_path = Path(urlparse(base_url).path)
                        else:
                            base_path = Path(base_url)
                            
                        if base_path.is_file():
                            base_dir = base_path.parent
                        else:
                            base_dir = base_path
                            
                        if src.startswith("file://"):
                            img_path = Path(urlparse(src).path)
                        else:
                            img_path = Path(src)
                            
                        if not img_path.is_absolute():
                            img_path = (base_dir / img_path).resolve()
                            
                        if img_path.exists() and img_path.is_file():
                            img_bytes = img_path.read_bytes()
                            mime_type, _ = mimetypes.guess_type(str(img_path))
                            if not mime_type:
                                mime_type = "image/jpeg"
                                
                            # Optimize the local image!
                            optimized = self._optimize_image(img_bytes, mime_type, max_dim=1200, quality=75, role=role)
                            if optimized:
                                opt_bytes, opt_mime = optimized
                                opt_ext = self._get_extension_from_mime(opt_mime)
                                if not opt_ext:
                                    opt_ext = img_path.suffix
                                epub_img_name = f"img_{idx}{opt_ext}"
                                images_to_pack.append((f"OEBPS/images/{epub_img_name}", opt_bytes, opt_mime))
                                img["src"] = f"images/{epub_img_name}"
                            else:
                                # Use original
                                epub_img_name = f"img_{idx}{img_path.suffix}"
                                images_to_pack.append((f"OEBPS/images/{epub_img_name}", img_bytes, mime_type))
                                img["src"] = f"images/{epub_img_name}"
                        else:
                            logger.warning(f"Local image path does not exist for EPUB: {img_path}")
                    except Exception as e:
                        logger.error(f"Error loading local image {src} for EPUB: {e}")
            
            # Execute downloads concurrently
            if download_tasks:
                results = await asyncio.gather(*download_tasks, return_exceptions=True)
                for img, idx, task, absolute_url in img_rewrites:
                    idx_task = download_tasks.index(task)
                    result = results[idx_task]
                    
                    if isinstance(result, Exception) or not result:
                        logger.warning(f"Failed to download image for EPUB {absolute_url}: {result}")
                    else:
                        img_bytes, mime_type = result
                        ext = self._get_extension_from_mime(mime_type)
                        if not ext:
                            ext = ".jpg"
                        epub_img_name = f"img_{idx}{ext}"
                        images_to_pack.append((f"OEBPS/images/{epub_img_name}", img_bytes, mime_type))
                        img["src"] = f"images/{epub_img_name}"
                        
        # Ensure all tables are wrapped correctly, similar to HTML
        for table in soup.find_all("table"):
            table.wrap(soup.new_tag("div", attrs={"class": "table-container"}))
            
        split_at = config.EPUB_SPLIT_AT
        toc_max_depth = config.EPUB_TOC_MAX_DEPTH
        body = soup.body if soup.body else soup
        split_tag = _normalize_split_tag(split_at)
        _assign_stable_heading_ids(soup, toc_max_depth=toc_max_depth)
        multi_chapter = len(body.find_all(split_tag)) > 1

        # Resolve EPUB metadata (dc:title, dc:date, dc:language, dc:creator)
        md = metadata or {}
        h1_tag = soup.find("h1")
        title_text = md.get("title") or (h1_tag.get_text() if h1_tag else "Saved Article")
        language = md.get("language") or detect_language(html_content)
        creator = md.get("creator") or md.get("author") or "Read It Later"
        date_text = md.get("date") or datetime.date.today().strftime("%Y-%m-%d")
        escaped_title = _xml_escape_text(title_text)
        escaped_creator = _xml_escape_text(str(creator))
        escaped_date = _xml_escape_text(str(date_text))
        escaped_language = _xml_escape_text(str(language))

        nav_map_xml, _nav_point_count = _build_ncx_nav_points(
            soup,
            title_text,
            toc_max_depth=toc_max_depth,
            multi_chapter=multi_chapter,
        )
        ncx_depth = str(max(1, min(int(toc_max_depth), 6)))

        chapters = split_html_into_chapters(
            soup,
            split_at=split_at,
            toc_max_depth=toc_max_depth,
            assign_ids=False,
        )

        if multi_chapter:
            chapter_xhtml_files = []
            for index, chapter in enumerate(chapters, start=1):
                chapter_xhtml_files.append((
                    f"OEBPS/chapter-{index:02d}.xhtml",
                    self._build_xhtml(chapter["title"], chapter["html_fragment"], language=language),
                ))
        else:
            chapter_xhtml_files = [(
                "OEBPS/article.xhtml",
                self._build_xhtml(title_text, chapters[0]["html_fragment"], language=language),
            )]
        
        # Pack everything into an EPUB zip in memory
        epub_io = io.BytesIO()
        book_uuid = str(uuid.uuid4())
        
        with zipfile.ZipFile(epub_io, "w", zipfile.ZIP_DEFLATED) as epub:
            # 1. mimetype (MUST be first and uncompressed!)
            epub.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
            
            # 2. container.xml
            container_xml = (
                '<?xml version="1.0" encoding="UTF-8"?>\n'
                '<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">\n'
                '  <rootfiles>\n'
                '    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>\n'
                '  </rootfiles>\n'
                '</container>'
            )
            epub.writestr("META-INF/container.xml", container_xml)
            
            # 3. style.css
            style_css = (
                'body {\n'
                '  font-family: -apple-system, system-ui, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;\n'
                '  line-height: 1.6;\n'
                '  margin: 5%;\n'
                '  color: #1a1a1a;\n'
                '}\n'
                'h1 {\n'
                '  font-size: 1.8em;\n'
                '  line-height: 1.2;\n'
                '  margin-bottom: 0.5em;\n'
                '}\n'
                'h2 {\n'
                '  font-size: 1.4em;\n'
                '  margin-top: 1.5em;\n'
                '  margin-bottom: 0.5em;\n'
                '}\n'
                'p {\n'
                '  margin-bottom: 1em;\n'
                '}\n'
                'img {\n'
                '  max-width: 100%;\n'
                '  height: auto;\n'
                '  display: inline-block;\n'
                '  vertical-align: middle;\n'
                '  border-radius: 4px;\n'
                '}\n'
                'img.illustration {\n'
                '  display: block;\n'
                '  margin: 1.5em auto;\n'
                '}\n'
                'blockquote {\n'
                '  margin: 1em 0;\n'
                '  padding-left: 1em;\n'
                '  border-left: 4px solid #ccc;\n'
                '  color: #555;\n'
                '  font-style: italic;\n'
                '}\n'
                'pre, code {\n'
                '  font-family: Consolas, Monaco, monospace;\n'
                '  background-color: #f4f4f4;\n'
                '  padding: 0.2em 0.4em;\n'
                '  border-radius: 3px;\n'
                '}\n'
                'pre {\n'
                '  padding: 1em;\n'
                '  overflow-x: auto;\n'
                '}\n'
                'table {\n'
                '  width: 100%;\n'
                '  border-collapse: collapse;\n'
                '  margin: 1.5em 0;\n'
                '}\n'
                'th, td {\n'
                '  border: 1px solid #ccc;\n'
                '  padding: 0.6em;\n'
                '  text-align: left;\n'
                '  vertical-align: top;\n'
                '}\n'
                'th {\n'
                '  background-color: #f2f2f2;\n'
                '  font-weight: bold;\n'
                '}\n'
                'table p {\n'
                '  margin: 0;\n'
                '  padding: 0;\n'
                '}\n'
                '.table-container {\n'
                '  width: 100%;\n'
                '  overflow-x: auto;\n'
                '  margin: 1.5em 0;\n'
                '}\n'
            )
            epub.writestr("OEBPS/style.css", style_css)
            
            # 4. Chapter XHTML files
            manifest_chapters = []
            spine_itemrefs = []
            for index, (chapter_path, chapter_xhtml) in enumerate(chapter_xhtml_files, start=1):
                epub.writestr(chapter_path, chapter_xhtml)
                chapter_href = chapter_path.replace("OEBPS/", "")
                if multi_chapter:
                    chapter_id = f"chapter-{index:02d}"
                else:
                    chapter_id = "content"
                manifest_chapters.append(
                    f'<item id="{chapter_id}" href="{chapter_href}" media-type="application/xhtml+xml"/>'
                )
                spine_itemrefs.append(f'    <itemref idref="{chapter_id}"/>')
            
            # 5. Pack images
            manifest_images = []
            for path, data, mime in images_to_pack:
                epub.writestr(path, data)
                img_id = Path(path).name.replace(".", "_")
                img_href = path.replace("OEBPS/", "")
                manifest_images.append(f'<item id="{img_id}" href="{img_href}" media-type="{mime}"/>')
                
            # 6. toc.ncx
            toc_ncx = (
                '<?xml version="1.0" encoding="UTF-8"?>\n'
                '<!DOCTYPE ncx PUBLIC "-//NISO//DTD ncx 2005-1//EN" "http://www.daisy.org/z3986/2005/ncx-2005-1.dtd">\n'
                '<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">\n'
                '  <head>\n'
                f'    <meta name="dtb:uid" content="urn:uuid:{book_uuid}"/>\n'
                f'    <meta name="dtb:depth" content="{ncx_depth}"/>\n'
                '    <meta name="dtb:totalPageCount" content="0"/>\n'
                '    <meta name="dtb:maxPageNumber" content="0"/>\n'
                '  </head>\n'
                '  <docTitle>\n'
                f'    <text>{escaped_title}</text>\n'
                '  </docTitle>\n'
                '  <navMap>\n'
                f'{nav_map_xml}'
                '  </navMap>\n'
                '</ncx>'
            )
            epub.writestr("OEBPS/toc.ncx", toc_ncx)
            
            # 7. content.opf
            manifest_img_str = "\n    ".join(manifest_images)
            manifest_chapter_str = "\n    ".join(manifest_chapters)
            spine_itemref_str = "\n".join(spine_itemrefs)
            content_opf = (
                '<?xml version="1.0" encoding="UTF-8"?>\n'
                '<package xmlns="http://www.idpf.org/2007/opf" unique-identifier="BookId" version="2.0">\n'
                '  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:opf="http://www.idpf.org/2007/opf">\n'
                f'    <dc:title>{escaped_title}</dc:title>\n'
                f'    <dc:language>{escaped_language}</dc:language>\n'
                f'    <dc:identifier id="BookId">urn:uuid:{book_uuid}</dc:identifier>\n'
                f'    <dc:creator>{escaped_creator}</dc:creator>\n'
                f'    <dc:date>{escaped_date}</dc:date>\n'
                '  </metadata>\n'
                '  <manifest>\n'
                '    <item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>\n'
                '    <item id="style" href="style.css" media-type="text/css"/>\n'
                f'    {manifest_chapter_str}\n'
                f'    {manifest_img_str}\n'
                '  </manifest>\n'
                '  <spine toc="ncx">\n'
                f'{spine_itemref_str}\n'
                '  </spine>\n'
                '</package>'
            )
            epub.writestr("OEBPS/content.opf", content_opf)
            
        return epub_io.getvalue()

    def _build_xhtml(self, title: str, body_content_or_soup, language: str = "en") -> str:
        """
        Build valid XHTML file content for the article.
        """
        escaped_title = _xml_escape_text(title)
        escaped_language = _xml_escape_text(language)
        if isinstance(body_content_or_soup, str):
            body_content = body_content_or_soup
        elif isinstance(body_content_or_soup, BeautifulSoup):
            soup = body_content_or_soup
            if soup.body:
                body_content = _serialize_xhtml_children(soup.body)
            else:
                body_content = _serialize_xhtml_children(soup)
        else:
            body_content = _serialize_xhtml_children(body_content_or_soup)
            
        return (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.1//EN" "http://www.w3.org/TR/xhtml11/DTD/xhtml11.dtd">\n'
            f'<html xmlns="http://www.w3.org/1999/xhtml" xml:lang="{escaped_language}">\n'
            '<head>\n'
            '  <meta http-equiv="Content-Type" content="application/xhtml+xml; charset=utf-8" />\n'
            f'  <title>{escaped_title}</title>\n'
            '  <link rel="stylesheet" href="style.css" type="text/css" />\n'
            '</head>\n'
            '<body>\n'
            f'{body_content}\n'
            '</body>\n'
            '</html>'
        )

