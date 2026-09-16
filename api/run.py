"""POST /api/run?limit=N - web demo of the food image agent.

Body: raw .xlsx bytes. Response: ZIP with the processed images, the
processing report (CSV) and a summary. Capped at DEMO_MAX_ITEMS items so a
run fits inside the 60 s serverless limit; the full 260-item batch is a CLI
job (python main.py). Drive upload is disabled here (dry-run).
"""

import io
import json
import os
import tempfile
import zipfile
from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlparse

MAX_ITEMS = int(os.getenv("DEMO_MAX_ITEMS", "5"))
MAX_UPLOAD_MB = 5


class handler(BaseHTTPRequestHandler):
    def _send_json(self, code: int, obj: dict):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass

    def do_GET(self):
        self._send_json(200, {"endpoint": "POST /api/run?limit=N",
                              "body": "raw .xlsx bytes", "returns": "application/zip"})

    def do_POST(self):
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length <= 0 or length > MAX_UPLOAD_MB * 1024 * 1024:
            return self._send_json(
                413, {"error": f"request body must be 1..{MAX_UPLOAD_MB} MB of .xlsx bytes"})

        data = self.rfile.read(length)
        q = parse_qs(urlparse(self.path).query)
        try:
            limit = max(1, min(int(q.get("limit", [MAX_ITEMS])[0]), MAX_ITEMS))
        except (TypeError, ValueError):
            limit = MAX_ITEMS

        try:
            payload = self._run_agent(data, limit)
        except Exception as e:
            return self._send_json(500, {"error": f"agent run failed: {e}"})

        self.send_response(200)
        self.send_header("Content-Type", "application/zip")
        self.send_header("Content-Disposition",
                         'attachment; filename="food_images.zip"')
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    @staticmethod
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
                dry_run=True,  # never touch Drive from the web demo
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
