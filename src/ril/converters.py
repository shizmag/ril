import os
import re
import io
import base64
import hashlib
import logging
import asyncio
import zipfile
import uuid
from abc import ABC, abstractmethod
from typing import Optional, Tuple, Dict, Any
from urllib.parse import urljoin, urlparse, parse_qs, urlunparse, urlencode, unquote, quote
from pathlib import Path
import httpx
from bs4 import BeautifulSoup
import markdownify

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
        referer: Optional[str] = None
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
                        "Chrome/122.0.0.0 Safari/537.36 (ReadItLaterBot/1.0; contact: support@readitlater-app.local)"
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
                res = self._optimize_image(response.content, content_type)
                if not res:
                    return None
                return res
            except Exception as e:
                logger.error(f"Error downloading image bytes {url}: {e}")
                return None

    def _optimize_image(self, img_bytes: bytes, content_type: str, max_dim: int = 1000, quality: int = 75) -> Optional[Tuple[bytes, str]]:
        """
        Optimize downloaded image bytes using Pillow:
        1. Resizes to max_dim (1000px) keeping aspect ratio.
        2. Compresses JPEGs and other formats with quality=75.
        3. Keeps PNG alpha transparency channel if present, otherwise converts to JPEG.
        4. Filters out tiny spacer/tracker images (size <= 16x16).
        """
        try:
            from PIL import Image
            
            img = Image.open(io.BytesIO(img_bytes))
            orig_format = img.format
            
            # Filter out tiny spacer/tracker images (e.g. 16x16 or smaller)
            w, h = img.size
            if w <= 16 and h <= 16:
                logger.info(f"Skipping tiny/tracker image with dimensions {w}x{h}")
                return None
                
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
                
            if has_transparency:
                # Save as PNG with optimization to keep transparency
                img.save(out_io, format="PNG", optimize=True)
                mime_type = "image/png"
            else:
                # Save as JPEG with quality compression
                if img.mode != "RGB":
                    img = img.convert("RGB")
                img.save(out_io, format="JPEG", quality=quality, optimize=True)
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


def preprocess_html(html: str) -> str:
    """Preprocess HTML to decompose useless elements, strip tracking, and unwrap meaningless tags."""
    soup = BeautifulSoup(html, "lxml")
    
    # 1. Strip useless tags completely
    useless_tags = ["script", "style", "meta", "noscript", "svg"]
    if config.DISABLE_IMAGES:
        useless_tags.append("img")
        
    for tag in soup.find_all(useless_tags):
        tag.decompose()
        
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
        
        # 1. Download images and rewrite URLs in HTML to reference IDs
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
                
        return markdown_content

    async def _prepare_base64_references(self, html_content: str, base_url: str) -> Tuple[str, Dict[str, str]]:
        """
        Finds all image tags, downloads the images concurrently, and converts them to base64 URIs.
        Replaces the img src in HTML with a reference ID (e.g. img_ref_1).
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
                
                # Check for base64 encoded images
                if src.startswith("data:image/"):
                    image_refs[ref_id] = src
                    continue
                
                # Resolve relative URLs
                absolute_url = urljoin(base_url, src)
                
                # Download bytes using the shared helper
                task = self._download_single_image_bytes(client, absolute_url, semaphore, base_url)
                download_tasks.append(task)
                img_rewrites.append((ref_id, task, absolute_url))
            
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

                absolute_url = urljoin(base_url, src)
                task = self._download_single_image_bytes(client, absolute_url, semaphore, base_url)
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


class EPUBConverter(BaseConverter):
    """
    Converts HTML content to a self-contained EPUB ebook containing downloaded images.
    """
    
    @property
    def file_extension(self) -> str:
        return ".epub"

    async def convert(self, html_content: str, base_url: str, article_slug: str) -> bytes:
        # 1. Preprocess HTML
        html_content = preprocess_html(html_content)
        
        # 2. Extract and download all images, mapping them to local EPUB paths
        soup = BeautifulSoup(html_content, "lxml")
        img_tags = soup.find_all("img")
        
        images_to_pack = []  # list of (epub_path, bytes, mime_type)
        semaphore = asyncio.Semaphore(3)
        
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
            download_tasks = []
            img_rewrites = []
            
            for idx, img in enumerate(img_tags):
                src = extract_real_image_src(img)
                if not src:
                    continue
                
                # Check for base64 encoded images
                if src.startswith("data:image/"):
                    # Decode base64 and add to images to pack
                    try:
                        pattern = re.compile(r'^data:image/(\w+);base64,(.*)$')
                        match = pattern.match(src)
                        if match:
                            ext = match.group(1)
                            img_bytes = base64.b64decode(match.group(2))
                            mime = f"image/{ext}"
                            epub_img_name = f"img_{idx}.{ext}"
                            images_to_pack.append((f"OEBPS/images/{epub_img_name}", img_bytes, mime))
                            img["src"] = f"images/{epub_img_name}"
                    except Exception as e:
                        logger.error(f"Error parsing inline base64 image: {e}")
                    continue
                
                absolute_url = urljoin(base_url, src)
                task = self._download_single_image_bytes(client, absolute_url, semaphore, base_url)
                download_tasks.append(task)
                img_rewrites.append((img, idx, task, absolute_url))
                
            if download_tasks:
                results = await asyncio.gather(*download_tasks, return_exceptions=True)
                for img, idx, task, absolute_url in img_rewrites:
                    idx_task = download_tasks.index(task)
                    result = results[idx_task]
                    
                    if isinstance(result, Exception) or not result:
                        logger.warning(f"Failed to download image for EPUB {absolute_url}: {result}")
                        # Fallback to the absolute URL in EPUB
                        img["src"] = absolute_url
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
            
        # Get title from h1
        h1_tag = soup.find("h1")
        title_text = h1_tag.get_text() if h1_tag else "Saved Article"
        
        # Build XHTML content
        xhtml_content = self._build_xhtml(title_text, soup)
        
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
                '  display: block;\n'
                '  margin: 1.5em auto;\n'
                '  border-radius: 4px;\n'
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
            )
            epub.writestr("OEBPS/style.css", style_css)
            
            # 4. article.xhtml
            epub.writestr("OEBPS/article.xhtml", xhtml_content)
            
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
                '    <meta name="dtb:depth" content="1"/>\n'
                '    <meta name="dtb:totalPageCount" content="0"/>\n'
                '    <meta name="dtb:maxPageNumber" content="0"/>\n'
                '  </head>\n'
                '  <docTitle>\n'
                f'    <text>{title_text}</text>\n'
                '  </docTitle>\n'
                '  <navMap>\n'
                '    <navPoint id="navpoint-1" playOrder="1">\n'
                '      <navLabel>\n'
                f'        <text>{title_text}</text>\n'
                '      </navLabel>\n'
                '      <content src="article.xhtml"/>\n'
                '    </navPoint>\n'
                '  </navMap>\n'
                '</ncx>'
            )
            epub.writestr("OEBPS/toc.ncx", toc_ncx)
            
            # 7. content.opf
            manifest_img_str = "\n    ".join(manifest_images)
            content_opf = (
                '<?xml version="1.0" encoding="UTF-8"?>\n'
                '<package xmlns="http://www.idpf.org/2007/opf" unique-identifier="BookId" version="2.0">\n'
                '  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:opf="http://www.idpf.org/2007/opf">\n'
                f'    <dc:title>{title_text}</dc:title>\n'
                '    <dc:language>ru</dc:language>\n'
                f'    <dc:identifier id="BookId">urn:uuid:{book_uuid}</dc:identifier>\n'
                '    <dc:creator>Read It Later</dc:creator>\n'
                '    <dc:date>2026-05-30</dc:date>\n'
                '  </metadata>\n'
                '  <manifest>\n'
                '    <item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>\n'
                '    <item id="style" href="style.css" media-type="text/css"/>\n'
                '    <item id="content" href="article.xhtml" media-type="application/xhtml+xml"/>\n'
                f'    {manifest_img_str}\n'
                '  </manifest>\n'
                '  <spine toc="ncx">\n'
                '    <itemref idref="content"/>\n'
                '  </spine>\n'
                '</package>'
            )
            epub.writestr("OEBPS/content.opf", content_opf)
            
        return epub_io.getvalue()

    def _build_xhtml(self, title: str, soup: BeautifulSoup) -> str:
        """
        Build valid XHTML file content for the article.
        """
        body_content = ""
        if soup.body:
            body_content = "".join(str(child) for child in soup.body.children)
        else:
            body_content = str(soup)
            
        return (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.1//EN" "http://www.w3.org/TR/xhtml11/DTD/xhtml11.dtd">\n'
            '<html xmlns="http://www.w3.org/1999/xhtml" xml:lang="ru">\n'
            '<head>\n'
            '  <meta http-equiv="Content-Type" content="application/xhtml+xml; charset=utf-8" />\n'
            f'  <title>{title}</title>\n'
            '  <link rel="stylesheet" href="style.css" type="text/css" />\n'
            '</head>\n'
            '<body>\n'
            f'{body_content}\n'
            '</body>\n'
            '</html>'
        )

