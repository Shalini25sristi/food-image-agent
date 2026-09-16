"""Root WSGI entrypoint for the Vercel deployment of the food image agent.

Routes:
  GET  /api/health          -> JSON status
  POST /api/run?limit=N     -> body: raw .xlsx bytes, response: ZIP with the
                               processed images + processing report

Static files (the demo UI, gallery, report) are served from public/ by Vercel.
The demo is capped at DEMO_MAX_ITEMS items per run so it fits the serverless
execution limit; the full 260-item batch is a CLI job (python main.py).
"""

import io
import json
import os
import tempfile
import zipfile
from urllib.parse import parse_qs

MAX_ITEMS = int(os.getenv("DEMO_MAX_ITEMS", "5"))
MAX_UPLOAD_MB = 5

_STATUS = {200: "200 OK", 404: "404 Not Found", 405: "405 Method Not Allowed",
           413: "413 Payload Too Large", 500: "500 Internal Server Error"}


def _respond(start_response, code, body, content_type="application/json",
             extra_headers=()):
    headers = [("Content-Type", content_type),
               ("Content-Length", str(len(body)))] + list(extra_headers)
    start_response(_STATUS[code], headers)
    return [body]


def _json(start_response, code, obj):
    return _respond(start_response, code, json.dumps(obj).encode())


def _run_agent(xlsx_bytes: bytes, limit: int) -> bytes:
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
            max_candidates=4,
            dry_run=True,  # never touch Google Drive from the web demo
        )
        items = read_food_items(excel_path, cfg.column)[:limit]
        reporter = run(cfg, items=items)

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
        return _json(start_response, 200, {
            "status": "ok",
            "agent": "food-image-agent",
            "max_items_per_run": MAX_ITEMS,
        })

    if path == "/api/run":
        if method != "POST":
            return _json(start_response, 405, {
                "endpoint": "POST /api/run?limit=N",
                "body": "raw .xlsx bytes",
                "returns": "application/zip",
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
            limit = max(1, min(int(parse_qs(environ.get("QUERY_STRING", ""))
                                   .get("limit", [MAX_ITEMS])[0]), MAX_ITEMS))
        except (TypeError, ValueError):
            limit = MAX_ITEMS

        try:
            payload = _run_agent(data, limit)
        except Exception as e:
            return _json(start_response, 500, {"error": f"agent run failed: {e}"})

        return _respond(start_response, 200, payload, "application/zip",
                        [("Content-Disposition",
                          'attachment; filename="food_images.zip"')])

    return _json(start_response, 404, {"error": "not found"})
