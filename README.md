# AI Agent for Automated Food Image Collection & Processing

**[Live demo -> https://food-image-agent.vercel.app](https://food-image-agent.vercel.app)**

Reads food item names from an Excel file, finds a suitable image online,
checks its quality automatically, processes it to menu spec (1800x1200 px,
<10 MB JPG), renames it with the exact food item name and uploads it to a
Google Drive folder. A processing report (Excel + CSV) is generated at the
end.

Pipeline: **Excel -> Image Search -> Quality Validation -> Processing ->
Rename -> Google Drive Upload -> Report**

## Features

- **Excel input** - reads any `.xlsx`, auto-detects the food-item column,
  handles duplicates/empty rows.
- **Multi-provider image search** - SerpAPI (Google Images) and Google
  Custom Search if API keys are set, with automatic fallback to DuckDuckGo
  Images (no API key required).
- **Automatic image-quality validation** (OpenCV): blur detection
  (Laplacian variance), resolution, exposure, aspect ratio, and
  saliency-based framing analysis (subject centred, not excessively
  zoomed in, not heavily cropped at the edges). Watermarked stock-photo
  sites (freepik, shutterstock, istock, ...) are filtered out.
- **Automatic re-selection** - candidates are downloaded/validated in
  parallel and the best-scoring one that passes is used (best-effort
  fallback if none pass).
- **Smart processing** (Pillow): saliency-aware crop to the 3:2 menu
  ratio, resize to exactly 1800x1200, JPEG compressed below 10 MB.
- **Exact renaming** to the food item name (`Paneer Butter Masala.jpg`).
- **Google Drive upload** via a service account; automatically updates
  an existing file with the same name instead of creating duplicates.
- **Difficult-name handling** - "Special Paneer Tikka Masala Half" is
  cleaned to `Paneer Tikka Masala` before searching.
- **Report** - `processing_report.xlsx` / `.csv` with Food Item,
  Image Found, Image Processed, Uploaded, Source, Status.
- **Resilient & fast** - per-item error isolation; one failure never stops
  the run; report is saved after every item. The web demo runs items and
  candidate downloads concurrently with a hard time budget.
- **Polished web demo** - bundled menu, one-click runs, live "search new
  pictures" with a server-side image proxy, inline previews, animations and
  a Drive-status banner.

## Setup

```bash
python3 -m venv .venv            # (on minimal systems: python3 -m venv --without-pip .venv)
source .venv/bin/activate
pip install -r requirements-full.txt   # full setup incl. OpenCV
# or: pip install -r requirements.txt  # slim setup (framing checks reduced)
```

## Run

```bash
python main.py --excel sample_input.xlsx
python main.py --excel "Assignment - Ai agent - Sheet1.xlsx"   # the full 260-item menu
```

Latest full run over the assignment sheet: **260/260 items processed, 0 failures**
(report: `processing_report.xlsx` / `.csv`, images in `output/`).

## Live web demo (Vercel)

**Deployed:** https://food-image-agent.vercel.app

A serverless wrapper around the agent lives in `index.py` with a polished
single-page front-end in `public/`:

- `GET /` - UI. The assignment sheet is **bundled** (`public/assignment_menu.xlsx`
  + `public/menu.json`), so you can run the agent on it with one click, or
  upload your own `.xlsx`.
- `POST /api/run?limit=N&format=json|zip` - raw `.xlsx` body -> JSON with
  base64 previews + report rows (default UI), or a ZIP with full images +
  report (`format=zip`). Capped at 5 items per run; dishes and candidate
  downloads run concurrently so a run finishes in seconds.
- `GET /api/search?item=NAME&limit=N` - live image search for one dish,
  returns candidate URLs (powers the "Search new pics" button). Results are
  cached.
- `GET /api/image?url=...&w=440` - server-side image proxy + thumbnail;
  makes search results display reliably even when the source blocks
  hotlinking (the UI loads originals first and only falls back to this).
- `GET /api/health` - status JSON (includes whether Google Drive is configured).

The demo disables Google Drive uploads and saves images locally; the UI shows
a Drive status banner. Set `DRIVE_FOLDER_ID` + a service-account
`credentials.json` and Drive uploads become active in the CLI (the serverless
demo stays read-only by design).

Deploy:

```bash
npx vercel@latest --prod        # repo already contains vercel.json
```

The heavy lifting (hundreds of items) stays a CLI job; the web endpoint is a
live demonstration of the same pipeline.

Useful options:

| Option | Description |
|---|---|
| `--excel PATH` | input Excel file (default `sample_input.xlsx`) |
| `--column NAME` | food-item column (default `Food Item`, auto-detected if missing) |
| `--limit N` | process only the first N items |
| `--max-candidates N` | candidate images tried per item (default 6) |
| `--dry-run` | skip Google Drive upload even if configured |
| `--strict` | fail items whose images fail validation (default: best-effort) |
| `-v` | debug logging |

Without Drive configuration the agent still works end-to-end and saves
the processed images into `output/` (the report notes this).

## Google Drive setup (optional, ~10 min)

1. Google Cloud Console -> create a project -> enable **Google Drive API**.
2. Create a **Service Account** (IAM -> Service Accounts) and download its
   JSON key; save it as `credentials.json` in the project folder.
3. In Google Drive, share the target folder with the service-account
   e-mail (user@...iam.gserviceaccount.com) as **Editor**.
4. Copy the folder ID from its URL
   (`https://drive.google.com/drive/folders/<FOLDER_ID>`).
5. `cp .env.example .env` and set `GOOGLE_APPLICATION_CREDENTIALS` and
   `DRIVE_FOLDER_ID`.

Re-running the agent updates existing files with the same name rather
than creating duplicates.

## Optional search API keys

DuckDuckGo works with no key but can be rate-limited. For higher
reliability set either in `.env`:

- `SERPAPI_KEY` - [serpapi.com](https://serpapi.com) (Google Images)
- `GOOGLE_CSE_KEY` + `GOOGLE_CSE_CX` - Google Custom Search JSON API

Providers are tried in that order and fall back automatically.

## Tech stack

Python 3.12 · pandas / openpyxl · Pillow · OpenCV (saliency, blur) ·
requests · DuckDuckGo / SerpAPI / Google Custom Search · Google Drive API ·
Vercel serverless (Python runtime) · vanilla HTML/CSS/JS front-end.

## Local web preview

```bash
python .devserver.py        # serves public/ + the API at http://localhost:3000
```

(On Vercel, `public/` is served statically and `index.py` runs as the
serverless function.)

## Project structure

```
main.py                 CLI entry point
agent/
  config.py             central configuration (target size, thresholds)
  excel_reader.py       read/clean food items from Excel
  search.py             multi-provider image search + query cleaning
  validate.py           quality checks (blur, framing, zoom, ...)
  process.py            saliency-aware crop, resize, compress
  drive.py              Google Drive upload (service account)
  report.py             Excel/CSV processing report
  pipeline.py           end-to-end orchestration (parallel + time-bounded)
index.py                Vercel serverless function (web demo API)
public/
  index.html            demo UI (animations, integrated menu, image search)
  menu.json             the 260 bundled dish names
  assignment_menu.xlsx  the bundled assignment sheet
  favicon.svg           site icon
  gallery/ + results.json   pre-computed batch-run gallery
  processing_report.*   full 260-item report (Excel + CSV)
vercel.json             Vercel configuration
.devserver.py           local preview server (gitignored; serves public/ + API)
sample_input.xlsx       sample input
Assignment - Ai agent - Sheet1.xlsx   real menu sheet (260 items)
```

## How the quality validation works

Each downloaded candidate is scored on:

- **Resolution** - must be at least 800x533 (higher resolution scores higher)
- **Blur** - Laplacian variance of the grayscale image
- **Exposure** - rejects too-dark / washed-out images
- **Aspect ratio** - rejects extreme panoramas
- **Framing** - a spectral-residual saliency map locates the subject;
  the agent checks that the subject is roughly centred and is not cut by
  the frame edges or filling the entire frame (excessively zoomed)

The agent ranks candidates by (meets target size, title-relevance,
resolution), validates them in parallel and uses the **best-scoring one that
passes** - or the best-scoring one overall in best-effort mode.

## Report columns

`Food Item | Image Found | Image Processed | Uploaded | Source | Status`

Status values: `SUCCESS - <drive link>`,
`SUCCESS - processed & saved locally (Drive upload not configured)`,
`PARTIAL: ...`, `FAILED: ...` (with reason). Quality warnings from
best-effort mode are appended to the status.
