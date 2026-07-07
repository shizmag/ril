import asyncio
import os
import shutil
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import ril.pipeline
from ril.pipeline import (
    PipelineConfig,
    epub_to_markdown,
    markdown_to_epub,
    process_single_url,
    validate_and_normalize_math
)


@pytest.fixture(autouse=True)
def isolate_pipeline_paths(tmp_path, monkeypatch):
    """
    Isolate TEMP_DIR and OUTPUT_DIR for all pipeline tests to avoid disk pollution.
    """
    temp_dir = tmp_path / "temp"
    output_dir = tmp_path / "output"
    temp_dir.mkdir()
    output_dir.mkdir()
    monkeypatch.setattr("ril.pipeline.TEMP_DIR", temp_dir)
    monkeypatch.setattr("ril.pipeline.OUTPUT_DIR", output_dir)
    return {"temp": temp_dir, "output": output_dir}


@pytest.fixture
def mock_playwright(mocker):
    """
    Mock Playwright browser context and page objects.
    """
    mock_p = MagicMock()
    mock_browser = AsyncMock()
    mock_context = AsyncMock()
    mock_page = AsyncMock()
    
    # Configure async context manager entry
    mock_p.__aenter__ = AsyncMock(return_value=mock_p)
    mock_p.chromium.launch = AsyncMock(return_value=mock_browser)
    mock_browser.new_context = AsyncMock(return_value=mock_context)
    mock_context.new_page = AsyncMock(return_value=mock_page)
    
    # Mock default HTML content and page measurements
    mock_page.content = AsyncMock(return_value="<html><head><title>Test Title</title></head><body><p>Hello SVG <svg></svg></p></body></html>")
    mock_page.goto = AsyncMock()
    mock_page.set_content = AsyncMock()
    mock_page.evaluate = AsyncMock(return_value=1200)  # Mock scrollHeight
    mock_page.pdf = AsyncMock()
    
    mocker.patch("ril.pipeline.async_playwright", return_value=mock_p)
    return mock_page


@pytest.fixture
def mock_httpx_download(mocker):
    """
    Mock httpx.AsyncClient.get to prevent actual downloads during tests.
    """
    mock_client = AsyncMock()
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.content = b"%PDF-1.4 mock content"
    mock_response.raise_for_status = MagicMock()
    mock_client.get = AsyncMock(return_value=mock_response)
    
    # Patch HTTPX Client context manager
    mocker.patch("httpx.AsyncClient", return_value=mock_client)
    return mock_client


@pytest.fixture
def mock_subprocess_exec(mocker):
    """
    Mock asyncio.create_subprocess_exec to intercept CLI tool invocations.
    """
    mock_proc = AsyncMock()
    mock_proc.communicate = AsyncMock(return_value=(b"stdout", b"stderr"))
    mock_proc.returncode = 0
    
    mock_exec = mocker.patch("asyncio.create_subprocess_exec", return_value=mock_proc)
    return mock_exec


def test_math_validation_and_normalization():
    """
    Verify LaTeX equations in various non-standard delimiters are correctly normalized to $...$ and $$...$$.
    """
    raw_md = "Here is inline math: \\(E=mc^2\\) and block: \\[a^2 + b^2 = c^2\\]"
    normalized = validate_and_normalize_math(raw_md)
    assert "inline math: $E=mc^2$" in normalized
    assert "block: $$a^2 + b^2 = c^2$$" in normalized


@pytest.mark.asyncio
async def test_pandoc_helpers(mock_subprocess_exec, isolate_pipeline_paths):
    """
    Verify Pandoc helper functions correctly invoke the subprocess with the expected CLI parameters.
    """
    temp_dir = isolate_pipeline_paths["temp"]
    md_file = temp_dir / "test.md"
    md_file.write_text("Hello Math $x=1$")
    epub_file = temp_dir / "test.epub"
    
    # Test MD -> EPUB
    await markdown_to_epub(md_file, epub_file)
    mock_subprocess_exec.assert_called_with(
        "/opt/homebrew/bin/pandoc",
        str(md_file),
        "-o",
        str(epub_file),
        "--mathjax",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE
    )
    
    # Test EPUB -> MD
    epub_file.write_text("dummy epub content")  # Ensure file exists
    await epub_to_markdown(epub_file, md_file)
    mock_subprocess_exec.assert_called_with(
        "/opt/homebrew/bin/pandoc",
        str(epub_file),
        "-t",
        "markdown",
        "-o",
        str(md_file),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE
    )


@pytest.mark.asyncio
async def test_config_default_behavior(
    mock_playwright,
    mock_subprocess_exec,
    isolate_pipeline_paths
):
    """
    Verify that when both force_ocr and rasterize_svg are False, the pipeline runs
    successfully without modifying process environments or injecting SVG rasterization scripts.
    """
    output_dir = isolate_pipeline_paths["output"]
    
    # We must seed a mock markdown file for the pipeline to locate inside output directory
    def side_effect(*args, **kwargs):
        if len(args) > 0:
            if args[0] == ril.pipeline.MARKER_PATH:
                try:
                    idx = args.index("--output_dir")
                    md_dir = Path(args[idx + 1])
                    md_dir.mkdir(parents=True, exist_ok=True)
                    (md_dir / "extracted.md").write_text("## Mock Document")
                except (ValueError, IndexError):
                    pass
            elif args[0] == ril.pipeline.PANDOC_PATH:
                try:
                    idx = args.index("-o")
                    out_path = Path(args[idx + 1])
                    out_path.write_text("mock output content")
                except (ValueError, IndexError):
                    pass
        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(return_value=(b"success", b""))
        mock_proc.returncode = 0
        return mock_proc

    mock_subprocess_exec.side_effect = side_effect
    
    config = PipelineConfig(rasterize_svg=False, force_ocr=False)
    await process_single_url("https://en.wikipedia.org/wiki/Scraping", config)
    
    # Verify Playwright didn't trigger rasterization script
    for call in mock_playwright.evaluate.call_args_list:
        script_arg = call[0][0]
        assert "rasterize_svg" not in script_arg
        
    # Verify marker-pdf env dict was either default or did not contain forced OCR configurations
    marker_call = [call for call in mock_subprocess_exec.call_args_list if call[0][0] == ril.pipeline.MARKER_PATH]
    assert len(marker_call) == 1
    call_kwargs = marker_call[0].kwargs
    env = call_kwargs.get("env", {})
    assert env.get("FORCE_OCR") is None
    assert env.get("EXTRACT_IMAGES") is None


@pytest.mark.asyncio
async def test_strategy_1_force_ocr(
    mock_playwright,
    mock_subprocess_exec,
    isolate_pipeline_paths
):
    """
    Verify that when config.force_ocr=True, the subprocess execution of marker-pdf
    receives FORCE_OCR="1" and EXTRACT_IMAGES="True" env variables while preserving
    existing systems environment.
    """
    def side_effect(*args, **kwargs):
        if len(args) > 0:
            if args[0] == ril.pipeline.MARKER_PATH:
                try:
                    idx = args.index("--output_dir")
                    md_dir = Path(args[idx + 1])
                    md_dir.mkdir(parents=True, exist_ok=True)
                    (md_dir / "extracted.md").write_text("OCR Data")
                except (ValueError, IndexError):
                    pass
            elif args[0] == ril.pipeline.PANDOC_PATH:
                try:
                    idx = args.index("-o")
                    out_path = Path(args[idx + 1])
                    out_path.write_text("mock output content")
                except (ValueError, IndexError):
                    pass
        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(return_value=(b"success", b""))
        mock_proc.returncode = 0
        return mock_proc

    mock_subprocess_exec.side_effect = side_effect
    
    config = PipelineConfig(rasterize_svg=False, force_ocr=True)
    
    with patch.dict(os.environ, {"MY_SYSTEM_KEY": "system_value"}):
        await process_single_url("https://en.wikipedia.org/wiki/OCR", config)
        
    # Check the subprocess call details
    marker_call = [call for call in mock_subprocess_exec.call_args_list if call[0][0] == ril.pipeline.MARKER_PATH]
    assert len(marker_call) == 1
    call_kwargs = marker_call[0].kwargs
    env = call_kwargs.get("env", {})
    
    assert env.get("FORCE_OCR") == "1"
    assert env.get("EXTRACT_IMAGES") == "True"
    assert env.get("MY_SYSTEM_KEY") == "system_value"


@pytest.mark.asyncio
async def test_strategy_2_svg_rasterization(
    mock_playwright,
    mock_subprocess_exec,
    isolate_pipeline_paths
):
    """
    Verify that when config.rasterize_svg=True, page.evaluate is invoked with the
    SVG-to-base64 canvas serialization script right before page.pdf runs.
    """
    def side_effect(*args, **kwargs):
        if len(args) > 0:
            if args[0] == ril.pipeline.MARKER_PATH:
                try:
                    idx = args.index("--output_dir")
                    md_dir = Path(args[idx + 1])
                    md_dir.mkdir(parents=True, exist_ok=True)
                    (md_dir / "extracted.md").write_text("SVG Data")
                except (ValueError, IndexError):
                    pass
            elif args[0] == ril.pipeline.PANDOC_PATH:
                try:
                    idx = args.index("-o")
                    out_path = Path(args[idx + 1])
                    out_path.write_text("mock output content")
                except (ValueError, IndexError):
                    pass
        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(return_value=(b"success", b""))
        mock_proc.returncode = 0
        return mock_proc

    mock_subprocess_exec.side_effect = side_effect
    
    config = PipelineConfig(rasterize_svg=True, force_ocr=False)
    await process_single_url("https://en.wikipedia.org/wiki/SVG", config)
    
    # Ensure evaluate was called with serialization script
    evaluated_scripts = [call[0][0] for call in mock_playwright.evaluate.call_args_list]
    has_rasterize_script = any("xml = new XMLSerializer().serializeToString(svg)" in script for script in evaluated_scripts if isinstance(script, str))
    assert has_rasterize_script is True


@pytest.mark.asyncio
async def test_error_handling_and_cleanup(
    mock_playwright,
    mock_subprocess_exec,
    isolate_pipeline_paths
):
    """
    Ensure that even if the SVG rasterization script fails (evaluates to error) or
    marker-pdf subprocess throws an exception, all temporary PDF files and intermediate output folders
    are cleaned up gracefully without crashing or polluting disk storage.
    """
    temp_dir = isolate_pipeline_paths["temp"]
    output_dir = isolate_pipeline_paths["output"]
    
    # Trigger an intentional error inside marker-pdf subprocess call
    mock_subprocess_exec.side_effect = RuntimeError("Marker crashed unexpectedly")
    
    config = PipelineConfig(rasterize_svg=True, force_ocr=True)
    
    with pytest.raises(RuntimeError, match="Marker crashed unexpectedly"):
        await process_single_url("https://en.wikipedia.org/wiki/Crash", config)
        
    # Check stateless cleanup post error
    temp_files = list(temp_dir.glob("*"))
    output_subdirs = [x for x in output_dir.iterdir() if x.is_dir()]
    
    # Temporary PDF file must be deleted
    assert len(temp_files) == 0
    # Intermediate output directory (uuid folder) must be deleted
    assert len(output_subdirs) == 0
