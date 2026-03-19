"""
PDF to Markdown converter.
Downloads a PDF from a URL and converts it to Markdown using a three-tier
fallback chain: docling → pdfplumber → OpenRouter Vision API.

Logic adapted from:
  pdf2md/convert_problematic_files.py   (docling + pdfplumber)
  pdf2md/convert_with_openrouter.py     (PyMuPDF → OpenRouter Vision)
"""

import base64
import os
import tempfile
from io import BytesIO
from pathlib import Path

import requests

# ── optional imports with graceful fallback ──────────────────────────────────

try:
    from docling.document_converter import DocumentConverter
    HAS_DOCLING = True
except ImportError:
    HAS_DOCLING = False
    print("Warning: docling not installed. Install with: pip install docling")

try:
    import pdfplumber
    HAS_PDFPLUMBER = True
except ImportError:
    HAS_PDFPLUMBER = False
    print("Warning: pdfplumber not installed. Install with: pip install pdfplumber")

try:
    import fitz  # PyMuPDF
    HAS_PYMUPDF = True
except ImportError:
    HAS_PYMUPDF = False
    print("Warning: PyMuPDF not installed. Install with: pip install pymupdf")

try:
    from PIL import Image
    HAS_PIL = True
except ImportError:
    HAS_PIL = False
    print("Warning: Pillow not installed. Install with: pip install pillow")


# ── readability check ─────────────────────────────────────────────────────────

def is_readable(text: str) -> bool:
    """
    Return True if text looks like real content rather than encoded garbage.
    Mirrors the check used in pdf2md/convert_with_openrouter.py.
    """
    if not text or len(text.strip()) < 100:
        return False
    sample = text[:500]
    readable_chars = sum(
        1 for c in sample if c.isalnum() or c in " .,;:!?-\n\t()[]{}"
    )
    total_chars = len([c for c in sample if not c.isspace()])
    if total_chars == 0:
        return False
    # Reject if more than 10 % of chars are forward-slashes (CID artifacts)
    if sample.count("/") > len(sample) * 0.1:
        return False
    return (readable_chars / total_chars) > 0.3


# ── PDF download ──────────────────────────────────────────────────────────────

def fetch_pdf(url: str, tmp_path: Path, timeout: int = 60) -> bool:
    """Stream-download a PDF from *url* to *tmp_path*. Returns True on success."""
    try:
        with requests.get(url, stream=True, timeout=timeout) as r:
            r.raise_for_status()
            tmp_path.write_bytes(r.content)
        return True
    except Exception as e:
        print(f"  [PDF] Download failed for {url}: {e}")
        return False


# ── conversion methods ────────────────────────────────────────────────────────

def convert_with_docling(pdf_path: Path) -> str | None:
    """Convert PDF using docling. Returns Markdown string or None."""
    if not HAS_DOCLING:
        return None
    try:
        print("    Trying docling…")
        converter = DocumentConverter()
        result = converter.convert(str(pdf_path))
        md = result.document.export_to_markdown()
        if is_readable(md):
            print("    ✓ docling succeeded")
            return md
        print("    ✗ docling output is not readable, trying next method")
        return None
    except Exception as e:
        print(f"    ✗ docling error: {e}")
        return None


def convert_with_pdfplumber(pdf_path: Path) -> str | None:
    """Convert PDF using pdfplumber text extraction. Returns Markdown string or None."""
    if not HAS_PDFPLUMBER:
        return None
    try:
        print("    Trying pdfplumber…")
        parts = []
        with pdfplumber.open(str(pdf_path)) as pdf:
            for page in pdf.pages:
                text = page.extract_text()
                if text:
                    parts.append(text)
        if not parts:
            print("    ✗ pdfplumber extracted no text")
            return None
        combined = "\n\n".join(parts)
        if is_readable(combined):
            print("    ✓ pdfplumber succeeded")
            return combined
        print("    ✗ pdfplumber output is not readable, trying next method")
        return None
    except Exception as e:
        print(f"    ✗ pdfplumber error: {e}")
        return None


def _image_to_base64(image: "Image.Image") -> str:
    buf = BytesIO()
    image.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode()


def _pdf_to_images(pdf_path: Path, dpi: int = 200) -> list | None:
    """Render each PDF page to a PIL Image using PyMuPDF."""
    if not HAS_PYMUPDF or not HAS_PIL:
        print("    ✗ PyMuPDF or Pillow not available for image rendering")
        return None
    try:
        doc = fitz.open(str(pdf_path))
        zoom = dpi / 72.0
        mat = fitz.Matrix(zoom, zoom)
        images = []
        for page in doc:
            pix = page.get_pixmap(matrix=mat)
            img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
            images.append(img)
            pix = None
        doc.close()
        return images
    except Exception as e:
        print(f"    ✗ PyMuPDF rendering error: {e}")
        return None


def _send_page_to_openrouter(
    image: "Image.Image",
    api_key: str,
    model: str,
    page_num: int,
) -> str | None:
    """Send a single page image to the OpenRouter Vision API."""
    img_b64 = _image_to_base64(image)
    payload = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": (
                            "Extract all text from this PDF page. "
                            "Preserve the structure, formatting, and layout as much as possible. "
                            "Return the text in markdown format. Include headings, code blocks, "
                            "tables, and any other content exactly as it appears. "
                            "Do not add page numbers or headers — just return the extracted content."
                        ),
                    },
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/png;base64,{img_b64}"},
                    },
                ],
            }
        ],
        "max_tokens": 4000,
    }
    try:
        response = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=120,
        )
        if response.status_code == 200:
            data = response.json()
            choices = data.get("choices", [])
            if choices:
                return choices[0]["message"]["content"]
            print(f"      Warning: empty response for page {page_num}")
            return None
        print(f"      OpenRouter API error {response.status_code}: {response.text[:200]}")
        return None
    except Exception as e:
        print(f"      OpenRouter request error (page {page_num}): {e}")
        return None


def convert_with_openrouter(
    pdf_path: Path,
    api_key: str,
    model: str = "openai/gpt-4o",
    dpi: int = 200,
) -> str | None:
    """
    Convert PDF via OpenRouter Vision API.
    Process: PDF → per-page PNG images → base64 → API → per-page Markdown → combined.
    Returns Markdown string or None.
    """
    if not api_key:
        print("    ✗ OPENROUTER_API_KEY not set, skipping OpenRouter fallback")
        return None

    print("    Trying OpenRouter Vision API…")
    images = _pdf_to_images(pdf_path, dpi=dpi)
    if not images:
        print("    ✗ Could not render PDF to images")
        return None

    print(f"    Sending {len(images)} page(s) to OpenRouter ({model})…")
    parts = []
    for i, image in enumerate(images, 1):
        md = _send_page_to_openrouter(image, api_key, model, i)
        if md:
            # Strip ```markdown ... ``` wrapper that some models add
            md = md.strip()
            if md.startswith("```markdown"):
                md = md[len("```markdown"):].lstrip("\n")
            if md.startswith("```"):
                md = md[3:].lstrip("\n")
            if md.endswith("```"):
                md = md[:-3].rstrip()
            if i > 1:
                parts.append("\n\n---\n\n")
            parts.append(md)
        else:
            parts.append(f"[Failed to extract content from page {i}]")

    combined = "".join(parts)
    if is_readable(combined):
        print(f"    ✓ OpenRouter succeeded ({len(images)} pages)")
        return combined
    print("    ✗ OpenRouter output is not readable")
    return None


# ── public entry point ────────────────────────────────────────────────────────

def convert_url_to_md(
    url: str,
    output_path: Path,
    openrouter_api_key: str = "",
    openrouter_model: str = "openai/gpt-4o",
) -> bool:
    """
    Download a PDF from *url* and convert to Markdown at *output_path*.
    Tries docling → pdfplumber → OpenRouter in order.
    Returns True on success, False if all methods fail.
    """
    with tempfile.TemporaryDirectory() as tmp_dir:
        pdf_path = Path(tmp_dir) / "input.pdf"

        print(f"  [PDF] Downloading {url}")
        if not fetch_pdf(url, pdf_path):
            return False

        md: str | None = None

        md = convert_with_docling(pdf_path)
        if md is None:
            md = convert_with_pdfplumber(pdf_path)
        if md is None:
            md = convert_with_openrouter(pdf_path, openrouter_api_key, openrouter_model)

        if md is None:
            print(f"  [PDF] All methods failed for {url}")
            return False

        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(md, encoding="utf-8")
        return True
