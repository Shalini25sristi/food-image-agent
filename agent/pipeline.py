"""End-to-end pipeline: Excel -> search -> validate -> process -> rename -> Drive."""

import logging
import os
import re
import tempfile
from urllib.parse import urlparse

import requests

from .config import Config
from .drive import DriveUploader
from .excel_reader import read_food_items
from .process import process_image
from .report import Reporter, ReportRow
from .search import build_query, search_images, ImageCandidate
from .validate import validate_image, ValidationResult

log = logging.getLogger(__name__)

_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"),
    "Accept": "image/avif,image/webp,image/*,*/*;q=0.8",
}
_GOOD_ENOUGH_SCORE = 60.0


def _safe_filename(name: str) -> str:
    """Keep the exact food item name, only replacing filesystem-illegal chars."""
    return re.sub(r'[\\/:*?"<>|]', "-", name).strip()


def _download(candidate: ImageCandidate, tmp_dir: str, cfg: Config) -> str | None:
    try:
        with requests.get(candidate.url, headers=_HEADERS, stream=True,
                          timeout=cfg.download_timeout, allow_redirects=True) as r:
            r.raise_for_status()
            ctype = r.headers.get("Content-Type", "").split(";")[0].lower()
            ext = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp",
                   "image/gif": ".gif", "image/bmp": ".bmp"}.get(ctype, ".img")
            path = os.path.join(tmp_dir, f"cand_{abs(hash(candidate.url))}{ext}")
            limit = cfg.max_download_mb * 1024 * 1024
            size = 0
            with open(path, "wb") as f:
                for chunk in r.iter_content(chunk_size=65536):
                    size += len(chunk)
                    if size > limit:
                        log.debug("Skipping %s - exceeds download cap", candidate.url)
                        f.close()
                        os.remove(path)
                        return None
                    f.write(chunk)
            return path
    except Exception as e:
        log.debug("Download failed for %s: %s", candidate.url, e)
        return None


def _select_image(item: str, cfg: Config, tmp_dir: str):
    """Search, download and validate candidates.

    Returns (tmp_path, candidate, validation, used_fallback) or (None,)*4.
    """
    query = build_query(item)
    log.info("Searching: %r (query: %r)", item, query)
    candidates = search_images(query, cfg)
    if not candidates:
        return None, None, None, False

    best_pass = None   # (score, path, candidate, validation)
    best_any = None
    for cand in candidates[: cfg.max_candidates]:
        path = _download(cand, tmp_dir, cfg)
        if not path:
            continue
        val = validate_image(path, cfg)
        entry = (val.score, path, cand, val)
        if best_any is None or val.score > best_any[0]:
            best_any = entry
        if val.ok:
            best_pass = entry
            log.info("Candidate accepted (score %.1f, %dx%d): %s",
                     val.score, val.width, val.height, cand.url[:90])
            if val.score >= _GOOD_ENOUGH_SCORE:
                break
        else:
            log.info("Candidate rejected (%s): %s", "; ".join(val.reasons), cand.url[:90])

    if best_pass:
        score, path, cand, val = best_pass
        return path, cand, val, False
    if cfg.best_effort and best_any:
        score, path, cand, val = best_any
        log.warning("No candidate passed validation for %r; using best effort "
                    "(score %.1f, issues: %s)", item, score, "; ".join(val.reasons))
        return path, cand, val, True
    return None, None, None, False


def process_item(item: str, cfg: Config, uploader: DriveUploader,
                 tmp_dir: str) -> ReportRow:
    row = ReportRow(food_item=item)

    tmp_path, cand, val, fallback = _select_image(item, cfg, tmp_dir)
    if tmp_path is None:
        row.status = "FAILED: no suitable image found online"
        return row
    row.image_found = "Yes"
    row.source = cand.url

    # Process (crop/resize/compress) and rename to the exact food item name.
    os.makedirs(cfg.output_dir, exist_ok=True)
    final_path = os.path.join(cfg.output_dir, _safe_filename(item) + ".jpg")
    try:
        _, size = process_image(tmp_path, final_path, cfg)
    except Exception as e:
        row.status = f"FAILED: image processing error ({e})"
        return row
    row.image_processed = "Yes"

    # Upload to Google Drive (or keep local copy if Drive is not configured).
    if uploader.enabled:
        try:
            link = uploader.upload(final_path, os.path.basename(final_path))
            row.uploaded = "Yes"
            row.status = f"SUCCESS - {link}"
        except Exception as e:
            row.status = f"PARTIAL: processed locally, Drive upload failed ({e})"
    else:
        row.status = "SUCCESS - saved locally (Drive not configured)"

    if fallback:
        row.status += f" | quality warning: {'; '.join(val.reasons)}"
    return row


def run(cfg: Config, items: list[str] | None = None) -> Reporter:
    if items is None:
        items = read_food_items(cfg.excel_path, cfg.column)
    if not items:
        raise ValueError("No food items found in the Excel file.")

    uploader = DriveUploader(cfg)
    reporter = Reporter()

    with tempfile.TemporaryDirectory(prefix="food_agent_") as tmp_dir:
        for i, item in enumerate(items, 1):
            log.info("=== [%d/%d] %s ===", i, len(items), item)
            try:
                row = process_item(item, cfg, uploader, tmp_dir)
            except Exception as e:  # one failure must never stop the run
                log.exception("Unexpected error while processing %r", item)
                row = ReportRow(food_item=item, status=f"FAILED: unexpected error ({e})")
            reporter.add(row)
            reporter.save(cfg.report_path)  # persist progress after every item
            log.info("Status: %s", row.status)

    reporter.save(cfg.report_path)
    return reporter
