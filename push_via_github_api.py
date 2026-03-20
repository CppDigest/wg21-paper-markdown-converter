"""
Push successful conversion outputs to another GitHub repository via the REST API
(Git blobs + tree + commit + ref update). Intended for GitHub Actions; reads
configuration from environment variables.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
from pathlib import Path

import requests

from output_naming import url_to_filename

API_VERSION = "2022-11-28"
GITHUB_API = "https://api.github.com"


def _headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": API_VERSION,
    }


def _api_request(
    session: requests.Session, method: str, path: str, token: str, **kwargs
) -> requests.Response:
    url = f"{GITHUB_API}{path}" if path.startswith("/") else f"{GITHUB_API}/{path}"
    r = session.request(method, url, headers=_headers(token), timeout=120, **kwargs)
    return r


def _parse_owner_repo(repo: str) -> tuple[str, str]:
    repo = repo.strip().strip("/")
    if "/" not in repo or repo.count("/") != 1:
        raise ValueError(f"Invalid repo (expected owner/name): {repo!r}")
    owner, name = repo.split("/", 1)
    if not owner or not name:
        raise ValueError(f"Invalid repo (expected owner/name): {repo!r}")
    return owner, name


def _normalize_prefix(target_path: str) -> str:
    p = target_path.strip().strip("/")
    return p


def _remote_file_path(prefix: str, filename: str) -> str:
    if not prefix:
        return filename.replace("\\", "/")
    return f"{prefix}/{filename}".replace("\\", "/")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Push successful .md files and result.json to a GitHub repo via API."
    )
    parser.add_argument(
        "--result-json",
        default=os.environ.get("RESULT_JSON", "result.json"),
        help="Path to result.json (default: result.json or RESULT_JSON)",
    )
    parser.add_argument(
        "--output-dir",
        default=os.environ.get("OUTPUT_DIR", "converted"),
        help="Directory with .md files (default: converted or OUTPUT_DIR)",
    )
    args = parser.parse_args()

    token = (os.environ.get("TARGET_REPO_TOKEN") or "").strip()
    repo_raw = (os.environ.get("TARGET_REPO") or "").strip()
    target_path = os.environ.get("TARGET_PATH", "") or ""
    branch = (os.environ.get("TARGET_BRANCH") or "main").strip() or "main"
    run_id = (os.environ.get("GITHUB_RUN_ID") or "local").strip()

    if not repo_raw or not token:
        print("Push skipped: set TARGET_REPO and TARGET_REPO_TOKEN to enable push.")
        return 0

    result_path = Path(args.result_json)
    output_dir = Path(args.output_dir)

    if not result_path.is_file():
        print(f"Push skipped: {result_path} not found.")
        return 0

    try:
        payload = json.loads(result_path.read_text(encoding="utf-8"))
        rows: list[dict] = payload.get("result") or []
    except (json.JSONDecodeError, OSError) as e:
        print(f"ERROR: cannot read {result_path}: {e}", file=sys.stderr)
        return 1

    successes = [r for r in rows if r.get("status") == "success"]
    if not successes:
        print("Push skipped: no successful conversions in result.json.")
        return 0

    try:
        owner, repo = _parse_owner_repo(repo_raw)
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1

    prefix = _normalize_prefix(target_path)
    by_remote: dict[str, Path] = {}

    for row in successes:
        url = (row.get("url") or "").strip()
        if not url:
            continue
        name = url_to_filename(url)
        local = output_dir / name
        if not local.is_file():
            print(f"Warning: success for {url!r} but missing file {local}, skipping.")
            continue
        by_remote[_remote_file_path(prefix, name)] = local

    by_remote[_remote_file_path(prefix, "result.json")] = result_path
    uploads = list(by_remote.items())

    if len(uploads) == 1:
        # only result.json
        print("Warning: only result.json to push (no .md files found on disk).")

    session = requests.Session()
    base = f"/repos/{owner}/{repo}"

    r = _api_request(session, "GET", f"{base}/git/ref/heads/{branch}", token)
    if r.status_code == 404:
        print(
            f"ERROR: branch {branch!r} not found on {owner}/{repo} (create it first).",
            file=sys.stderr,
        )
        return 1
    r.raise_for_status()
    tip_sha = r.json()["object"]["sha"]

    r = _api_request(session, "GET", f"{base}/git/commits/{tip_sha}", token)
    r.raise_for_status()
    base_tree = r.json()["tree"]["sha"]

    tree_entries: list[dict] = []
    for remote_path, local_path in uploads:
        data = local_path.read_bytes()
        b64 = base64.b64encode(data).decode("ascii")
        r = _api_request(
            session,
            "POST",
            f"{base}/git/blobs",
            token,
            json={"content": b64, "encoding": "base64"},
        )
        r.raise_for_status()
        blob_sha = r.json()["sha"]
        tree_entries.append(
            {
                "path": remote_path,
                "mode": "100644",
                "type": "blob",
                "sha": blob_sha,
            }
        )

    r = _api_request(
        session,
        "POST",
        f"{base}/git/trees",
        token,
        json={"base_tree": base_tree, "tree": tree_entries},
    )
    r.raise_for_status()
    new_tree_sha = r.json()["sha"]

    commit_msg = f"markdown-converter: workflow run {run_id}"
    r = _api_request(
        session,
        "POST",
        f"{base}/git/commits",
        token,
        json={
            "message": commit_msg,
            "tree": new_tree_sha,
            "parents": [tip_sha],
        },
    )
    r.raise_for_status()
    new_commit_sha = r.json()["sha"]

    r = _api_request(
        session,
        "PATCH",
        f"{base}/git/refs/heads/{branch}",
        token,
        json={"sha": new_commit_sha},
    )
    r.raise_for_status()

    print(f"Pushed {len(uploads)} file(s) to {owner}/{repo}@{branch} ({new_commit_sha[:7]}).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
