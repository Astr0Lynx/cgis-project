"""Tiny upstream stand-in for gateway routing tests: echoes method, path and Host as JSON."""
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

NAME = os.environ.get("MOCK_NAME", "mock")


class Echo(BaseHTTPRequestHandler):
    def _reply(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length:
            self.rfile.read(length)
        body = json.dumps({"service": NAME, "method": self.command, "path": self.path,
                           "host": self.headers.get("Host"),
                           "x_forwarded_for": self.headers.get("X-Forwarded-For")}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    do_GET = do_POST = do_HEAD = do_PUT = do_DELETE = _reply

    def log_message(self, *args):
        pass


ThreadingHTTPServer(("0.0.0.0", int(os.environ.get("MOCK_PORT", "8000"))), Echo).serve_forever()
