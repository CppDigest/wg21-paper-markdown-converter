"""
url2md.py — Main entry point for the URL-to-Markdown converter.

Accepts a JSON payload {"papers": ["url1", "url2", ...]} via --papers,
converts each URL to Markdown, writes a result.json summary, and zips
the output folder.

Designed to be called by the GitHub Actions workflow_dispatch job, but
also runnable locally.

On GitHub Actions, ``.github/workflows/convert.yml`` uploads ``<output-dir>.zip``
and ``result.json`` as an artifact, then optionally pushes successful ``.md``
files plus ``result.json`` to another repository via the GitHub API when
``TARGET_REPO`` and ``TARGET_REPO_TOKEN`` are configured.
"""

import argparse
import json
import os
import sys
import zipfile
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv

import html_converter
import pdf_converter
from output_naming import url_to_filename

load_dotenv()


# ── URL type detection ────────────────────────────────────────────────────────


def detect_type(url: str) -> str:
    """
    Return "pdf" or "html" based on URL path suffix first, then Content-Type
    header. Falls back to "html" if detection is inconclusive.
    """
    parsed = urlparse(url)
    if parsed.path.lower().endswith(".pdf"):
        return "pdf"

    try:
        import requests

        head = requests.head(url, allow_redirects=True, timeout=15)
        ct = head.headers.get("Content-Type", "").lower()
        if "pdf" in ct:
            return "pdf"
    except Exception:
        pass

    return "html"


# ── zip helper ────────────────────────────────────────────────────────────────


def zip_folder(folder: Path, zip_path: Path) -> None:
    """Zip all files inside *folder* into *zip_path*."""
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for file in folder.rglob("*"):
            if file.is_file():
                zf.write(file, file.relative_to(folder))
    print(f"Zipped output folder → {zip_path}")


# ── main ──────────────────────────────────────────────────────────────────────


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Fetch URLs, convert to Markdown, produce result.json and a zip archive."
    )
    parser.add_argument(
        "--papers",
        required=True,
        help='JSON string: {"papers": ["url1", "url2", ...]}',
    )
    parser.add_argument(
        "--output-dir",
        default="converted",
        help="Folder to write converted .md files (default: converted)",
    )
    parser.add_argument(
        "--model",
        default=os.getenv("OPENROUTER_MODEL", "openai/gpt-4o"),
        help="OpenRouter vision model for PDF fallback (default: openai/gpt-4o)",
    )
    args = parser.parse_args()

    # ── parse input JSON ──────────────────────────────────────────────────────
    try:
        payload = json.loads(args.papers)
        urls: list[str] = payload["papers"]
        if not isinstance(urls, list) or not urls:
            raise ValueError("'papers' must be a non-empty list of URLs")
    except (json.JSONDecodeError, KeyError, ValueError) as e:
        print(f"ERROR: Invalid --papers value: {e}", file=sys.stderr)
        print(
            'Expected format: --papers \'{"papers": ["url1", "url2"]}\'',
            file=sys.stderr,
        )
        return 1

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    openrouter_api_key = os.getenv("OPENROUTER_API_KEY", "")
    if not openrouter_api_key:
        print(
            "Warning: OPENROUTER_API_KEY not set — OpenRouter PDF fallback will be skipped"
        )

    # ── convert each URL ──────────────────────────────────────────────────────
    results: list[dict] = []

    for url in urls:
        url = url.strip()
        if not url:
            continue

        file_type = detect_type(url)
        out_file = output_dir / url_to_filename(url)

        print(f"\n{'='*70}")
        print(f"URL  : {url}")
        print(f"Type : {file_type}")
        print(f"Out  : {out_file}")
        print(f"{'='*70}")

        if file_type == "pdf":
            ok = pdf_converter.convert_url_to_md(
                url,
                out_file,
                openrouter_api_key=openrouter_api_key,
                openrouter_model=args.model,
            )
        else:
            ok = html_converter.convert_url_to_md(url, out_file)

        status = "success" if ok else "failed"
        results.append({"url": url, "status": status})
        print(f"→ {status.upper()}")

    # ── write result.json ─────────────────────────────────────────────────────
    all_ok = all(r["status"] == "success" for r in results)
    message = (
        "convert finished successfully"
        if all_ok
        else "convert finished with errors"
    )

    result_payload = {"message": message, "result": results}
    result_path = Path("result.json")
    result_path.write_text(
        json.dumps(result_payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"\nWrote {result_path}")

    # ── zip output folder ─────────────────────────────────────────────────────
    zip_path = Path(f"{args.output_dir}.zip")
    zip_folder(output_dir, zip_path)

    # ── summary ───────────────────────────────────────────────────────────────
    success_count = sum(1 for r in results if r["status"] == "success")
    failed_count = len(results) - success_count

    print(f"\n{'='*70}")
    print(f"Done: {success_count} succeeded, {failed_count} failed")
    print(f"Message: {message}")
    print(f"{'='*70}")

    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
