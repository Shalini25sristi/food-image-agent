"""End-to-end pipeline: Excel -> search -> validate -> process -> rename -> Drive."""

import logging
import os
import re
import tempfile
import uuid
from concurrent.futures import ThreadPoolExecutor, wait
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
            path = os.path.join(tmp_dir, f"cand_{uuid.uuid4().hex}{ext}")
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


def _download_and_validate(cand: ImageCandidate, tmp_dir: str, cfg: Config):
    path = _download(cand, tmp_dir, cfg)
    if not path:
        return None
    val = validate_image(path, cfg)
    return (val.score, path, cand, val)


def _select_image(item: str, cfg: Config, tmp_dir: str):
    """Search, download and validate candidates (downloads run in parallel).

    Returns (tmp_path, candidate, validation, used_fallback) or (None,)*4.
    """
    query = build_query(item)
    log.info("Searching: %r (query: %r)", item, query)
    candidates = search_images(query, cfg)
    if not candidates:
        return None, None, None, False

    pool = candidates[: cfg.max_candidates]
    results = []
    workers = max(1, min(getattr(cfg, "candidate_workers", 4), len(pool)))
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for r in ex.map(lambda c: _download_and_validate(c, tmp_dir, cfg), pool):
            if r:
                results.append(r)
    if not results:
        return None, None, None, False

    passed = [r for r in results if r[3].ok]
    if passed:
        score, path, cand, val = max(passed, key=lambda r: r[0])
        log.info("Candidate accepted (score %.1f, %dx%d): %s",
                 val.score, val.width, val.height, cand.url[:90])
        return path, cand, val, False

    for _, _, cand, val in results:
        log.info("Candidate rejected (%s): %s", "; ".join(val.reasons), cand.url[:90])
    if cfg.best_effort:
        score, path, cand, val = max(results, key=lambda r: r[0])
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
        row.status = "SUCCESS - processed & saved locally (Drive upload not configured)"

    if fallback:
        row.status += f" | quality warning: {'; '.join(val.reasons)}"
    return row


def _process_safe(item: str, cfg: Config, uploader: DriveUploader,
                  tmp_dir: str) -> ReportRow:
    try:
        return process_item(item, cfg, uploader, tmp_dir)
    except Exception as e:  # one failure must never stop the run
        log.exception("Unexpected error while processing %r", item)
        return ReportRow(food_item=item, status=f"FAILED: unexpected error ({e})")


def _run_parallel(items: list[str], cfg: Config, uploader: DriveUploader,
                  tmp_dir: str, reporter: Reporter, workers: int,
                  budget: float | None) -> None:
    """Process several dishes concurrently; each is still isolated.

    `budget` is an overall wall-clock limit in seconds - items that do not
    finish in time are reported as timed out instead of hanging the request.
    """
    rows: dict[str, ReportRow] = {}
    ex = ThreadPoolExecutor(max_workers=max(1, min(workers, len(items))))
    try:
        futures = {ex.submit(_process_safe, it, cfg, uploader, tmp_dir): it
                   for it in items}
        done, pending = wait(futures, timeout=budget)
        for fut in done:
            rows[futures[fut]] = fut.result()
        for fut in pending:
            fut.cancel()
            rows[futures[fut]] = ReportRow(
                food_item=futures[fut], status="FAILED: timed out (network too slow)")
    finally:
        # Never block the response on a stuck network thread.
        ex.shutdown(wait=False, cancel_futures=True)
    for item in items:
        row = rows.get(item) or ReportRow(
            food_item=item, status="FAILED: not processed")
        reporter.add(row)
        reporter.save(cfg.report_path)
        log.info("Status: %s", row.status)


def run(cfg: Config, items: list[str] | None = None,
        workers: int = 1, budget: float | None = None) -> Reporter:
    if items is None:
        items = read_food_items(cfg.excel_path, cfg.column)
    if not items:
        raise ValueError("No food items found in the Excel file.")

    uploader = DriveUploader(cfg)
    reporter = Reporter()

    with tempfile.TemporaryDirectory(prefix="food_agent_") as tmp_dir:
        if workers and workers > 1 and len(items) > 1:
            _run_parallel(items, cfg, uploader, tmp_dir, reporter, workers, budget)
        else:
            for i, item in enumerate(items, 1):
                log.info("=== [%d/%d] %s ===", i, len(items), item)
                row = _process_safe(item, cfg, uploader, tmp_dir)
                reporter.add(row)
                reporter.save(cfg.report_path)  # persist progress after every item
                log.info("Status: %s", row.status)

    reporter.save(cfg.report_path)
    return reporter
