"""Root WSGI entrypoint for the Vercel deployment of the food image agent.

Routes:
  GET  /api/health              -> JSON status
  GET  /api/search?item=NAME&limit=N
                                -> JSON list of candidate images found online
  POST /api/run?limit=N[&format=zip|json]
                                -> body: raw .xlsx bytes
                                   format=zip (default): ZIP with the processed
                                   images + processing report
                                   format=json: JSON with base64 images + report
                                   rows (used by the live demo UI)

Static files (the demo UI, integrated menu sheet, gallery, report) are served
from public/ by Vercel. The demo is capped at DEMO_MAX_ITEMS items per run so
it fits the serverless execution limit; the full 260-item batch is a CLI job
(python main.py).
"""

import base64
import io
import json
import os
import re
import tempfile
import time
import zipfile
from urllib.parse import parse_qs, urlparse

MAX_ITEMS = int(os.getenv("DEMO_MAX_ITEMS", "5"))
MAX_SEARCH = 12
MAX_UPLOAD_MB = 5
_SEARCH_TTL = 900  # seconds to cache search results in-memory
_search_cache: dict = {}

_STATUS = {200: "200 OK", 400: "400 Bad Request", 404: "404 Not Found",
           405: "405 Method Not Allowed", 413: "413 Payload Too Large",
           500: "500 Internal Server Error", 502: "502 Bad Gateway"}


def _respond(start_response, code, body, content_type="application/json",
             extra_headers=()):
    headers = [("Content-Type", content_type),
               ("Content-Length", str(len(body)))] + list(extra_headers)
    start_response(_STATUS[code], headers)
    return [body]


def _json(start_response, code, obj):
    return _respond(start_response, code, json.dumps(obj).encode())


def _safe_filename(name: str) -> str:
    return re.sub(r'[\\/:*?"<>|]', "-", name).strip()


def _thumbnail_b64(path: str, max_px: int = 720, quality: int = 72) -> str | None:
    """Small base64 JPEG preview so the JSON response stays under the
    serverless response-size limit (full images are in the ZIP download)."""
    try:
        from PIL import Image, ImageOps
        with Image.open(path) as im:
            im = ImageOps.exif_transpose(im).convert("RGB")
            im.thumbnail((max_px, max_px), Image.LANCZOS)
            buf = io.BytesIO()
            im.save(buf, "JPEG", quality=quality, optimize=True)
        return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()
    except Exception:
        return None


def _query(environ, key, default=None):
    return parse_qs(environ.get("QUERY_STRING", "")).get(key, [default])[0]


_PROXY_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"),
    "Accept": "image/avif,image/webp,image/*,*/*;q=0.8",
}


def _proxy_image(url: str, width: int) -> bytes:
    """Fetch a remote image server-side and return a small JPEG.

    Browsers often block hotlinked images (referrer / CORS); proxying and
    downscaling makes every search result display reliably and keeps the
    response small.
    """
    import requests
    from PIL import Image, ImageOps

    scheme = url.split(":", 1)[0].lower()
    if scheme not in ("http", "https"):
        raise ValueError("only http(s) image URLs are allowed")
    origin = f"{scheme}://{urlparse(url).netloc}/"
    headers = dict(_PROXY_HEADERS, Referer=origin)
    resp = requests.get(url, headers=headers, timeout=15,
                        allow_redirects=True, stream=True)
    resp.raise_for_status()
    ctype = (resp.headers.get("Content-Type") or "").split(";")[0].lower()
    if not ctype.startswith("image/"):
        raise ValueError(f"not an image (content-type {ctype or 'unknown'})")
    raw = resp.raw.read(12 * 1024 * 1024, decode_content=True)
    with Image.open(io.BytesIO(raw)) as im:
        im = ImageOps.exif_transpose(im).convert("RGB")
        im.thumbnail((width, width * 2), Image.LANCZOS)
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=80, optimize=True)
    return buf.getvalue()


def _search(item: str, limit: int) -> list[dict]:
    from agent.config import Config
    from agent.search import build_query, search_images

    key = (item.strip().lower(), limit)
    now = time.time()
    hit = _search_cache.get(key)
    if hit and hit[0] > now:
        return hit[1]

    cfg = Config(max_candidates=limit)
    candidates = search_images(build_query(item), cfg)
    results = [{
        "url": c.url,
        "width": c.width,
        "height": c.height,
        "title": c.title,
        "source": c.source,
    } for c in candidates[:limit]]
    _search_cache[key] = (now + _SEARCH_TTL, results)
    return results


def _execute(xlsx_bytes: bytes, limit: int, fmt: str) -> bytes:
    from agent.config import Config
    from agent.excel_reader import read_food_items
    from agent.pipeline import run

    with tempfile.TemporaryDirectory(prefix="food_web_") as tmp:
        excel_path = os.path.join(tmp, "input.xlsx")
        with open(excel_path, "wb") as f:
            f.write(xlsx_bytes)

        cfg = Config(
            excel_path=excel_path,
            output_dir=os.path.join(tmp, "out"),
            report_path=os.path.join(tmp, "report.xlsx"),
            max_candidates=3,
            download_timeout=6,
            dry_run=True,  # never touch Google Drive from the web demo
        )
        items = read_food_items(excel_path, cfg.column)[:limit]
        # Dishes run concurrently and the whole batch is bounded so the
        # serverless request always returns quickly.
        reporter = run(cfg, items=items, workers=min(6, len(items)), budget=45)

        if fmt == "json":
            results = []
            for row in reporter.rows:
                entry = row.as_dict()
                path = os.path.join(cfg.output_dir, _safe_filename(row.food_item) + ".jpg")
                if os.path.exists(path):
                    entry["bytes"] = os.path.getsize(path)
                    preview = _thumbnail_b64(path)
                    if preview:
                        entry["image"] = preview
                results.append(entry)
            return json.dumps({
                "summary": reporter.summary(),
                "total": len(reporter.rows),
                "succeeded": sum(1 for r in reporter.rows
                                 if r.status.startswith("SUCCESS")),
                "results": results,
            }).encode()

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            for name in sorted(os.listdir(cfg.output_dir)):
                z.write(os.path.join(cfg.output_dir, name), f"images/{name}")
            z.write(os.path.splitext(cfg.report_path)[0] + ".csv",
                    "processing_report.csv")
            z.writestr("summary.txt", reporter.summary() + "\n")
        return buf.getvalue()


def app(environ, start_response):
    path = environ.get("PATH_INFO", "/")
    method = environ.get("REQUEST_METHOD", "GET")

    if path == "/api/health":
        from agent.config import Config
        cfg = Config()
        drive_ready = bool(cfg.drive_folder_id) and os.path.exists(cfg.drive_credentials)
        return _json(start_response, 200, {
            "status": "ok",
            "agent": "food-image-agent",
            "max_items_per_run": MAX_ITEMS,
            "drive_configured": drive_ready,
        })

    if path == "/api/image":
        url = (_query(environ, "url", "") or "").strip()
        if not url:
            return _json(start_response, 400, {"error": "missing ?url="})
        try:
            width = max(120, min(int(_query(environ, "w", 440)), 1200))
        except (TypeError, ValueError):
            width = 440
        try:
            img = _proxy_image(url, width)
        except Exception as e:
            return _json(start_response, 502, {"error": f"image fetch failed: {e}"})
        return _respond(start_response, 200, img, "image/jpeg",
                        [("Cache-Control", "public, max-age=86400, immutable")])

    if path == "/api/search":
        if method not in ("GET", "POST"):
            return _json(start_response, 405, {
                "endpoint": "GET /api/search?item=NAME&limit=N"})
        item = (_query(environ, "item", "") or "").strip()
        if not item:
            return _json(start_response, 400, {"error": "missing ?item=NAME"})
        try:
            limit = max(1, min(int(_query(environ, "limit", MAX_SEARCH)), MAX_SEARCH))
        except (TypeError, ValueError):
            limit = MAX_SEARCH
        try:
            results = _search(item, limit)
        except Exception as e:
            return _json(start_response, 500, {"error": f"search failed: {e}"})
        body = json.dumps({
            "item": item, "count": len(results), "results": results}).encode()
        return _respond(start_response, 200, body, "application/json",
                        [("Cache-Control", "public, max-age=600")])

    if path == "/api/run":
        if method != "POST":
            return _json(start_response, 405, {
                "endpoint": "POST /api/run?limit=N[&format=zip|json]",
                "body": "raw .xlsx bytes",
                "returns": "application/zip or application/json",
            })
        try:
            length = int(environ.get("CONTENT_LENGTH") or 0)
        except ValueError:
            length = 0
        if length <= 0 or length > MAX_UPLOAD_MB * 1024 * 1024:
            return _json(start_response, 413, {
                "error": f"request body must be 1..{MAX_UPLOAD_MB} MB of .xlsx bytes"})

        data = environ["wsgi.input"].read(length)
        try:
            limit = max(1, min(int(_query(environ, "limit", MAX_ITEMS)), MAX_ITEMS))
        except (TypeError, ValueError):
            limit = MAX_ITEMS

        fmt = (_query(environ, "format", "zip") or "zip").lower()
        if fmt not in ("zip", "json"):
            fmt = "zip"

        try:
            payload = _execute(data, limit, fmt)
        except Exception as e:
            return _json(start_response, 500, {"error": f"agent run failed: {e}"})

        if fmt == "json":
            return _respond(start_response, 200, payload, "application/json")
        return _respond(start_response, 200, payload, "application/zip",
                        [("Content-Disposition",
                          'attachment; filename="food_images.zip"')])

    return _json(start_response, 404, {"error": "not found"})
