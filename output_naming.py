"""Shared URL → output `.md` basename logic (used by url2md and push_via_github_api)."""

from pathlib import Path
from urllib.parse import urlparse


def url_to_filename(url: str) -> str:
    """Derive a safe .md filename from the URL path stem."""
    parsed = urlparse(url)
    stem = Path(parsed.path).stem or "index"
    safe = "".join(c if (c.isalnum() or c in "-_.") else "_" for c in stem)
    return safe + ".md"
