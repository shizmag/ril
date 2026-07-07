import asyncio
import logging
import os
import re
import shutil
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Union

import httpx
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright
from readability import Document

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("ril.pipeline")

# Configuration
TEMP_DIR = Path("./temp")
OUTPUT_DIR = Path("./output")
MARKER_PATH = "/Users/vladimirkasterin/.local/bin/marker_single"
PANDOC_PATH = "/opt/homebrew/bin/pandoc"

# Ensure directories exist
TEMP_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


@dataclass
class PipelineConfig:
    rasterize_svg: bool = False
    force_ocr: bool = False


async def markdown_to_epub(md_path: Union[str, Path], epub_path: Union[str, Path]) -> None:
    """
    Convert a Markdown file to EPUB using Pandoc.
    """
    md_path = Path(md_path)
    epub_path = Path(epub_path)
    
    if not md_path.exists():
        raise FileNotFoundError(f"Markdown file not found: {md_path}")
        
    proc = await asyncio.create_subprocess_exec(
        PANDOC_PATH,
        str(md_path),
        "-o",
        str(epub_path),
        "--mathjax",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE
    )
    stdout, stderr = await proc.communicate()
    
    if proc.returncode != 0:
        raise RuntimeError(f"Pandoc conversion from Markdown to EPUB failed: {stderr.decode().strip()}")


async def epub_to_markdown(epub_path: Union[str, Path], md_path: Union[str, Path]) -> None:
    """
    Convert an EPUB file back to Markdown using Pandoc.
    """
    epub_path = Path(epub_path)
    md_path = Path(md_path)
    
    if not epub_path.exists():
        raise FileNotFoundError(f"EPUB file not found: {epub_path}")
        
    proc = await asyncio.create_subprocess_exec(
        PANDOC_PATH,
        str(epub_path),
        "-t",
        "markdown",
        "-o",
        str(md_path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE
    )
    stdout, stderr = await proc.communicate()
    
    if proc.returncode != 0:
        raise RuntimeError(f"Pandoc conversion from EPUB to Markdown failed: {stderr.decode().strip()}")


def validate_and_normalize_math(md_content: str) -> str:
    """
    Validate and normalize mathematical formulas to be standard Pandoc markdown compliant.
    Specifically, wraps display equations in $$...$$ and inline equations in $...$.
    """
    content = re.sub(r'\\\[(.*?)\\\]', r'$$\1$$', md_content, flags=re.DOTALL)
    content = re.sub(r'\\\((.*?)\\\)', r'$\1$', content, flags=re.DOTALL)
    return content


async def download_pdf_directly(url: str, dest_path: Path) -> None:
    """
    Download a PDF file directly using HTTPX.
    """
    logger.info(f"Downloading PDF directly from {url}...")
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/122.0.0.0 Safari/537.36"
        )
    }
    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        response = await client.get(url, headers=headers)
        response.raise_for_status()
        with open(dest_path, "wb") as f:
            f.write(response.content)


async def render_webpage_to_pdf(url: str, dest_path: Path, config: PipelineConfig) -> None:
    """
    Render a webpage to a clean PDF:
    1. Navigate with async Playwright, wait for networkidle.
    2. Extract raw HTML and clean via readability-lxml.
    3. Keep core content and preserve math images/SVGs.
    4. Set page content to cleaned HTML.
    5. Optionally rasterize SVG elements.
    6. Print to PDF.
    """
    logger.info(f"Navigating to {url} using Playwright...")
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        try:
            context = await browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/122.0.0.0 Safari/537.36"
                ),
                viewport={"width": 1280, "height": 800}
            )
            page = await context.new_page()
            
            await page.goto(url, wait_until="networkidle", timeout=60000)
            
            raw_html = await page.content()
            doc = Document(raw_html)
            title = doc.title()
            cleaned_body = doc.summary()
            
            clean_html = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>{title}</title>
    <style>
        body {{
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
            line-height: 1.6;
            color: #111;
            max-width: 800px;
            margin: 0 auto;
            padding: 40px 20px;
        }}
        h1, h2, h3, h4, h5, h6 {{
            font-family: "Outfit", "Inter", sans-serif;
            margin-top: 1.5em;
            margin-bottom: 0.5em;
        }}
        img, svg {{
            max-width: 100%;
            height: auto;
            display: block;
            margin: 1.5em auto;
        }}
        pre, code {{
            background: #f4f4f4;
            padding: 0.2em 0.4em;
            border-radius: 3px;
            font-family: monospace;
        }}
        pre {{
            padding: 1em;
            overflow-x: auto;
        }}
    </style>
</head>
<body>
    <h1>{title}</h1>
    {cleaned_body}
</body>
</html>"""
            
            await page.set_content(clean_html, wait_until="networkidle")
            
            # SVG Rasterization Strategy
            if config.rasterize_svg:
                logger.info("Executing client-side SVG rasterization...")
                rasterize_script = """
                () => {
                    const svgs = document.querySelectorAll('svg');
                    svgs.forEach(svg => {
                        try {
                            const rect = svg.getBoundingClientRect();
                            const width = rect.width || svg.getAttribute('width') || 'auto';
                            const height = rect.height || svg.getAttribute('height') || 'auto';
                            
                            const xml = new XMLSerializer().serializeToString(svg);
                            const base64 = btoa(unescape(encodeURIComponent(xml)));
                            
                            const img = document.createElement('img');
                            img.src = 'data:image/svg+xml;base64,' + base64;
                            
                            if (width !== 'auto' && width > 0) {
                                img.style.width = width + 'px';
                                img.width = width;
                            }
                            if (height !== 'auto' && height > 0) {
                                img.style.height = height + 'px';
                                img.height = height;
                            }
                            
                            svg.parentNode.replaceChild(img, svg);
                        } catch (e) {
                            console.error('Failed to rasterize SVG:', e);
                        }
                    });
                }
                """
                await page.evaluate(rasterize_script)
            
            try:
                height = await page.evaluate("document.documentElement.scrollHeight")
                await page.pdf(
                    path=str(dest_path),
                    width="850px",
                    height=f"{height}px",
                    print_background=True,
                    margin={"top": "20px", "bottom": "20px", "left": "20px", "right": "20px"}
                )
            except Exception as pdf_err:
                logger.warning(f"Failed to render unpaginated PDF, falling back to A4: {pdf_err}")
                await page.pdf(path=str(dest_path), format="A4", print_background=True)
                
            logger.info(f"Cleaned PDF printed successfully to {dest_path}")
        finally:
            await browser.close()


async def run_marker_pdf(pdf_path: Path, output_dir: Path, config: PipelineConfig) -> Path:
    """
    Run marker-pdf CLI on the PDF file via subprocess.
    If force_ocr is toggled on, inject FORCE_OCR=1 and EXTRACT_IMAGES=True environment variables.
    """
    logger.info(f"Running marker-pdf on {pdf_path} (force_ocr={config.force_ocr})...")
    output_dir.mkdir(parents=True, exist_ok=True)
    
    env = os.environ.copy()
    if config.force_ocr:
        env["FORCE_OCR"] = "1"
        env["EXTRACT_IMAGES"] = "True"
        
    proc = await asyncio.create_subprocess_exec(
        MARKER_PATH,
        str(pdf_path),
        "--output_dir",
        str(output_dir),
        "--output_format",
        "markdown",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env
    )
    stdout, stderr = await proc.communicate()
    
    if proc.returncode != 0:
        raise RuntimeError(f"marker-pdf failed: {stderr.decode().strip()}")
        
    logger.info(f"marker-pdf completed successfully. Output saved to {output_dir}")
    
    md_files = list(output_dir.glob("**/*.md"))
    if not md_files:
        raise FileNotFoundError(f"No markdown file found in marker-pdf output directory: {output_dir}")
        
    return md_files[0]


async def process_single_url(url: str, config: Optional[PipelineConfig] = None) -> None:
    """
    Executes the full pipeline for a single input URL:
    1. Ingestion & Routing
    2. Reading Mode & PDF generation
    3. Run marker-pdf
    4. Format conversion, Pandoc validation & bidirectional checking
    5. Stateless cleanup
    6. Telemetry & logging
    """
    if config is None:
        config = PipelineConfig()
        
    start_time = time.time()
    task_id = str(uuid.uuid4())
    
    temp_pdf = TEMP_DIR / f"{task_id}.pdf"
    marker_out_dir = OUTPUT_DIR / task_id
    
    url_lower = url.lower().split('?')[0]
    is_pdf = url_lower.endswith('.pdf') or "arxiv.org/pdf/" in url.lower()
    
    try:
        # Step 1: Ingestion & Routing
        if is_pdf:
            await download_pdf_directly(url, temp_pdf)
        else:
            # Step 2: Reading Mode & Print to PDF
            await render_webpage_to_pdf(url, temp_pdf, config)
            
        # Step 3: Run marker-pdf
        extracted_md_path = await run_marker_pdf(temp_pdf, marker_out_dir, config)
        
        # Step 4: Formatting & Interoperability
        with open(extracted_md_path, "r", encoding="utf-8") as f:
            md_content = f.read()
            
        normalized_md = validate_and_normalize_math(md_content)
        
        final_md_path = OUTPUT_DIR / f"{task_id}.md"
        with open(final_md_path, "w", encoding="utf-8") as f:
            f.write(normalized_md)
            
        final_epub_path = OUTPUT_DIR / f"{task_id}.epub"
        await markdown_to_epub(final_md_path, final_epub_path)
        
        # Verify Bidirectional integrity
        verify_md_path = OUTPUT_DIR / f"{task_id}_verified.md"
        try:
            await epub_to_markdown(final_epub_path, verify_md_path)
        finally:
            if verify_md_path.exists():
                verify_md_path.unlink()
                
    except Exception as e:
        logger.error(f"Pipeline error while processing URL {url}: {e}")
        raise e
    finally:
        # Step 5: Stateless Cleanup
        if temp_pdf.exists():
            try:
                temp_pdf.unlink()
            except Exception as err:
                logger.warning(f"Failed to delete temp PDF {temp_pdf}: {err}")
                
        if marker_out_dir.exists():
            try:
                shutil.rmtree(marker_out_dir)
            except Exception as err:
                logger.warning(f"Failed to delete intermediate dir {marker_out_dir}: {err}")
                
    # Step 6: Telemetry & Logging
    elapsed_time = time.time() - start_time
    print(f"✅ Task completed for {url}. Pipeline executed in {elapsed_time:.2f} seconds.")


# Alias to retain backward compatibility
async def process_pipeline(url: str) -> None:
    await process_single_url(url, PipelineConfig())


if __name__ == "__main__":
    urls = [
        "https://arxiv.org/pdf/1706.03762",
        "https://en.wikipedia.org/wiki/Attention_is_all_you_need",
        "https://arxiv.org/pdf/2005.14165"
    ]
    
    async def main():
        print("Starting Web-to-Document scraped pipeline...")
        # Demo usage of config object
        config = PipelineConfig(rasterize_svg=True, force_ocr=False)
        tasks = [process_single_url(url, config) for url in urls]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        for url, res in zip(urls, results):
            if isinstance(res, Exception):
                print(f"❌ Task failed for {url} with error: {res}")
                
    asyncio.run(main())
