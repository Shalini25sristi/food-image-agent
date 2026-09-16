# AI Agent for Automated Food Image Collection & Processing

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
- **Automatic re-selection** - if the first candidate fails validation,
  the agent keeps trying the next candidates and picks the best one.
- **Smart processing** (Pillow): saliency-aware crop to the 3:2 menu
  ratio, resize to exactly 1800x1200, JPEG compressed below 10 MB.
- **Exact renaming** to the food item name (`Paneer Butter Masala.jpg`).
- **Google Drive upload** via a service account; automatically updates
  an existing file with the same name instead of creating duplicates.
- **Difficult-name handling** - "Special Paneer Tikka Masala Half" is
  cleaned to `Paneer Tikka Masala` before searching.
- **Report** - `processing_report.xlsx` / `.csv` with Food Item,
  Image Found, Image Processed, Uploaded, Source, Status.
- **Resilient** - per-item error isolation; one failure never stops the
  run; report is saved after every item.

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

A serverless wrapper around the agent lives in `api/index.py` with a small
front-end in `public/`:

- `GET /` - UI: upload an .xlsx, get back a ZIP with processed images + report
- `POST /api/run?limit=N` - raw .xlsx body -> ZIP (capped at 5 items per run
  to fit the 60 s serverless limit; Drive upload is disabled in the demo)
- `GET /api/health` - status JSON

Deploy:

```bash
vercel --prod        # repo already contains vercel.json
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
  pipeline.py           end-to-end orchestration per food item
api/index.py            Vercel serverless function (web demo)
public/                 demo front-end + batch-run gallery
vercel.json             Vercel configuration
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

The agent tries candidates in order of (meets target size, title-relevance,
resolution) and uses the first one that passes - or the best-scoring one
in best-effort mode.

## Report columns

`Food Item | Image Found | Image Processed | Uploaded | Source | Status`

Status values: `SUCCESS - <drive link>`, `SUCCESS - saved locally`,
`PARTIAL: ...`, `FAILED: ...` (with reason). Quality warnings from
best-effort mode are appended to the status.
