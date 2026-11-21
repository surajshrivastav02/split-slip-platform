"""Optional offline smoke test, no pip installation required: py local.py"""
import os
from pathlib import Path
from http.server import HTTPServer, SimpleHTTPRequestHandler
from urllib.parse import urlsplit
from api.slip import handler

ROOT = Path(__file__).resolve().parent
os.environ["SPLIT_LOCAL_DB"] = str(ROOT / "local.sqlite3")

class Local(handler, SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT / "public"), **kwargs)

    def do_GET(self):
        if urlsplit(self.path).path == "/api/slip":
            self.dispatch()
        else:
            SimpleHTTPRequestHandler.do_GET(self)

    def list_directory(self, path):
        self.send_error(404)

if __name__ == "__main__":
    print("Open http://127.0.0.1:8000", flush=True)
    HTTPServer(("127.0.0.1", 8000), Local).serve_forever()
