# markdown-converter

Fetch a list of document URLs, convert each page to **Markdown**, and produce a **ZIP** of the `.md` files plus a **`result.json`** summary. Works for **HTML** pages and **PDF** files.

Designed for **local runs** and for **GitHub Actions** (`workflow_dispatch`). **Nothing is pushed to Git** — outputs are files on disk or workflow **artifacts**.

---

## Features

| Input | Pipeline |
|-------|----------|
| **HTML / `.htm`** | Fetch → preprocess (e.g. `<main>` body fix for committee-style HTML) → [Pandoc](https://pandoc.org/) (GFM) → post-process |
| **PDF** | Download → **docling** → **pdfplumber** → **OpenRouter** vision (PDF pages as images), in that order |

URL type is chosen from the path (`.pdf`) and, when needed, from `Content-Type` on a `HEAD` request.

Output filenames come from the URL path stem (e.g. `.../report.pdf` → `report.md`). Characters unsafe in file names are replaced with `_`.

---

## Repository layout

```
markdown-converter/
├── url2md.py              # CLI entry point
├── html_converter.py      # HTML → Markdown
├── pdf_converter.py       # PDF → Markdown (docling → pdfplumber → OpenRouter)
├── requirements.txt
├── .env.example           # Copy to .env for local secrets
├── README.md
└── .github/workflows/
    └── convert.yml        # Manual workflow + artifact upload
```

---

## Prerequisites (local)

- **Python 3.11+** recommended (matches CI).
- **Pandoc** installed and on `PATH` (used by `html_converter`).  
  - Windows: [Pandoc installers](https://pandoc.org/installing.html)  
  - macOS: `brew install pandoc`  
  - Ubuntu: `sudo apt install pandoc`
- **OpenRouter API key** (optional but strongly recommended for hard PDFs): [openrouter.ai/keys](https://openrouter.ai/keys)

PDF conversion pulls in **heavy** dependencies (e.g. docling / torch). First `pip install` can take a while.

---

## Local setup

```bash
cd markdown-converter
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env               # then edit OPENROUTER_API_KEY
```

---

## Usage

### `--papers` JSON

Pass a **JSON string** with a `papers` array of absolute URLs:

```json
{"papers": ["https://example.com/doc.html", "https://example.com/paper.pdf"]}
```

### CLI

Quick test using public fixtures (HTML + **real multi-page PDF**):

- **HTML:** [httpbin.org/html](https://httpbin.org/html) — short sample HTML page  
- **PDF:** [Mozilla pdf.js sample (TraceMonkey PLDI ’09)](https://mozilla.github.io/pdf.js/web/compressed.tracemonkey-pldi-09.pdf) — full research paper PDF; good for checking docling / pdfplumber / OpenRouter on non-trivial text  

```bash
python url2md.py \
  --papers '{"papers":["https://httpbin.org/html","https://mozilla.github.io/pdf.js/web/compressed.tracemonkey-pldi-09.pdf"]}' \
  --output-dir converted \
  --model openai/gpt-4o
```

**Minimal PDF (optional):** If you only need a tiny file to verify download + pipeline wiring, the [W3C dummy PDF](https://www.w3.org/WAI/ER/tests/xhtml/testfiles/resources/pdf/dummy.pdf) is one page with almost no body text — the resulting Markdown will look “empty” by design.

The Mozilla sample above is **larger and slower** (good for realistic output); use the W3C dummy only for a fast smoke test.

| Argument | Default | Description |
|----------|---------|-------------|
| `--papers` | *(required)* | JSON string: `{"papers": ["url", ...]}` |
| `--output-dir` | `converted` | Directory for `.md` files |
| `--model` | `openai/gpt-4o` or `OPENROUTER_MODEL` from env | OpenRouter model for PDF vision fallback |

### Exit code

- `0` — every URL converted successfully  
- `1` — at least one failure (still writes `result.json` and zips the folder when possible)

---

## Outputs

After a run you get:

1. **`<output-dir>/`** — one `*.md` per URL (best-effort; failed URLs do not create a good file).
2. **`result.json`** (in the current working directory) — summary for automation:

```json
{
  "message": "convert finished successfully",
  "result": [
    { "url": "https://example.com/a.html", "status": "success" },
    { "url": "https://example.com/b.pdf", "status": "failed" }
  ]
}
```

- `message` is `convert finished with errors` if any URL failed.
3. **`<output-dir>.zip`** — ZIP of the output folder (stdlib `zipfile`).

---

## GitHub Actions

Workflow: [`.github/workflows/convert.yml`](.github/workflows/convert.yml)

1. **Actions** → **Convert URLs to Markdown** → **Run workflow**
2. Fill **`papers`** with the same JSON string as `--papers` (one line).
3. Optionally change **`output_dir`** or **`openrouter_model`**.

### Repository secret

| Secret | Purpose |
|--------|---------|
| `OPENROUTER_API_KEY` | PDF vision fallback (optional if docling/pdfplumber succeed) |

### Artifacts (workflow upload)

After `url2md.py` writes **`<output_dir>.zip`** and **`result.json`**, the workflow uploads both files as one **GitHub Actions artifact** (step *Upload zip and result.json*):

- **Artifact name:** `converted-markdown-run-<run_number>-<run_attempt>` (unique per run / retry).
- **Contents:** `<output_dir>.zip` and `result.json` next to each other in the downloaded ZIP from the Actions UI.
- **Retention:** 90 days (see `retention-days` in the workflow).
- The upload step uses **`if: always()`** so you still get artifacts after partial conversion failures, when those files exist.

**Workflow artifact vs release asset — which to use?**

| | **Actions workflow artifact** (what this repo uses) | **Release** assets |
|---|-----------------------------------------------------|---------------------|
| **Best for** | CI outputs: “download the zip from this run” | Shipping a **versioned** build users expect to keep (e.g. v1.2.0 binaries) |
| **Lifetime** | Limited retention (e.g. 90 days on free plans unless you change policy) | Tied to the release/tag; typically long-lived |
| **Setup** | One `upload-artifact` step — no tag or release notes | Requires creating/editing a [Release](https://docs.github.com/en/repositories/releasing-projects-on-github/about-releases) and uploading assets (extra workflow steps) |

For **on-demand conversion** triggered by `workflow_dispatch`, **workflow artifacts are the better default**: simple, no release noise, good enough for “grab this run’s zip.” Use **Releases** only if you need permanent, versioned downloads for end users.

Local runs do **not** upload to GitHub; only the workflow does.

### Example (`gh` CLI)

```bash
gh workflow run convert.yml \
  -f papers='{"papers":["https://httpbin.org/html","https://mozilla.github.io/pdf.js/web/compressed.tracemonkey-pldi-09.pdf"]}' \
  -f output_dir=converted
```

---

## Limitations

- **Old or scanned PDFs** may produce poor text from docling/pdfplumber. **OpenRouter** often helps but is not guaranteed; some documents need dedicated OCR or manual cleanup.
- **CI time and size**: docling + models make the workflow slow and large; consider smaller URL batches or HTML-only jobs when possible.
- **HEAD requests**: Some servers misbehave on `HEAD`; type detection then falls back to treating non-`.pdf` URLs as HTML.

---

