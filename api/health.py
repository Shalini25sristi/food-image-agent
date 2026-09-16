"""GET /api/health - liveness/status endpoint."""

import json
import os
from http.server import BaseHTTPRequestHandler

MAX_ITEMS = int(os.getenv("DEMO_MAX_ITEMS", "5"))


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        body = json.dumps({
            "status": "ok",
            "agent": "food-image-agent",
            "max_items_per_run": MAX_ITEMS,
        }).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass
