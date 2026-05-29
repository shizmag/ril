import os
import re
import base64
import hashlib
import logging
import asyncio
from abc import ABC, abstractmethod
from typing import Optional, Tuple
from urllib.parse import urljoin, urlparse
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


class MarkdownConverter(BaseConverter):
    """
    Converts HTML content to Markdown and downloads images locally.
    """
    
    @property
    def file_extension(self) -> str:
        return ".md"

    async def convert(self, html_content: str, base_url: str, article_slug: str) -> str:
        # 1. Download images and rewrite URLs in HTML
        logger.info(f"Downloading images for article: {article_slug}")
        html_with_local_images = await self._download_images(html_content, base_url, article_slug)
        
        # 2. Convert HTML to Markdown
        # heading_style="ATX" generates "# Header" instead of Setext style "Header\n===="
        logger.info("Converting HTML to Markdown")
        markdown_content = markdownify.markdownify(
            html_with_local_images,
            heading_style="ATX",
            code_language_callback=lambda el: el.get("class", [""])[0].replace("language-", "") if el.get("class") else ""
        )
        
        # 3. Clean up extra newlines
        markdown_content = re.sub(r'\n{3,}', '\n\n', markdown_content)
        
        return markdown_content

    async def _download_images(self, html_content: str, base_url: str, article_slug: str) -> str:
        """
        Finds all image tags, downloads the images concurrently, and rewrites the image src.
        """
        soup = BeautifulSoup(html_content, "lxml")
        img_tags = soup.find_all("img")
        
        if not img_tags:
            return str(soup)

        # Output directory for images
        article_img_dir = config.LIBRARY_DIR / "images" / article_slug
        article_img_dir.mkdir(parents=True, exist_ok=True)
        
        # Keep track of downloads to run them concurrently
        download_tasks = []
        img_rewrites = []
        
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
            for idx, img in enumerate(img_tags):
                src = img.get("src")
                if not src:
                    continue
                
                # Check for base64 encoded images
                if src.startswith("data:image/"):
                    # Process inline base64 image immediately
                    filename = self._save_base64_image(src, article_slug, idx)
                    if filename:
                        img["src"] = f"images/{article_slug}/{filename}"
                    continue
                
                # Resolve relative URLs
                absolute_url = urljoin(base_url, src)
                
                # Generate unique filename based on URL hash
                url_hash = hashlib.md5(absolute_url.encode("utf-8")).hexdigest()
                
                # Schedule download task
                task = self._download_single_image(client, absolute_url, article_img_dir, url_hash)
                download_tasks.append(task)
                img_rewrites.append((img, task))
            
            # Execute all downloads concurrently
            results = await asyncio.gather(*download_tasks, return_exceptions=True)
            
            # Rewrite image sources with relative paths
            for img, task in img_rewrites:
                # Find index of this task in download_tasks to get the result
                idx = download_tasks.index(task)
                result = results[idx]
                
                if isinstance(result, Exception):
                    logger.warning(f"Failed to download image: {result}")
                    # Leave image source as is (or absolute URL if it was relative)
                    src = img.get("src")
                    if src and not src.startswith("data:"):
                        img["src"] = urljoin(base_url, src)
                elif result:
                    # Successfully saved locally, update the src to relative path
                    # E.g. 'images/2026-05-29_slug/abcd123.jpg'
                    relative_path = f"images/{article_slug}/{result}"
                    img["src"] = relative_path
                    
        return str(soup)

    async def _download_single_image(
        self,
        client: httpx.AsyncClient,
        url: str,
        save_dir: Path,
        url_hash: str
    ) -> Optional[str]:
        """
        Download a single image and save it to the specified folder.
        Returns the filename if successful, otherwise None.
        """
        try:
            # Add basic headers to prevent bot-detection on image requests
            headers = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"}
            response = await client.get(url, headers=headers)
            response.raise_for_status()
            
            content_type = response.headers.get("content-type", "")
            ext = self._get_extension_from_mime(content_type)
            
            if not ext:
                # Try getting extension from path
                parsed_url = urlparse(url)
                ext = Path(parsed_url.path).suffix.lower()
                if not ext or len(ext) > 5:
                    ext = ".jpg"  # Default fallback
            
            filename = f"{url_hash}{ext}"
            file_path = save_dir / filename
            
            # Save bytes to disk
            with open(file_path, "wb") as f:
                f.write(response.content)
                
            return filename
        except Exception as e:
            logger.error(f"Error downloading image {url}: {e}")
            return None

    def _save_base64_image(self, base64_str: str, article_slug: str, idx: int) -> Optional[str]:
        """
        Save a base64 encoded image to disk.
        Returns the filename if successful, otherwise None.
        """
        try:
            # Format is usually: data:image/png;base64,iVBORw0KGgo...
            pattern = re.compile(r'^data:image/(\w+);base64,(.*)$')
            match = pattern.match(base64_str)
            if not match:
                return None
                
            ext = f".{match.group(1)}"
            data_bytes = base64.b64decode(match.group(2))
            
            filename = f"inline_{idx}{ext}"
            save_path = config.LIBRARY_DIR / "images" / article_slug / filename
            
            with open(save_path, "wb") as f:
                f.write(data_bytes)
                
            return filename
        except Exception as e:
            logger.error(f"Error decoding base64 image: {e}")
            return None

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
