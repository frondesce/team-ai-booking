import json
import logging
import sys
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn
from typing import Optional, Dict

from app.config import AppConfig
from app.database import Database
from app.proxy import ProxyHandler
from app.web import WebHandler

logger = logging.getLogger("server")

class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True
    allow_reuse_address = True

def make_request_handler(db: Database, config: AppConfig):
    web_handler = WebHandler(db, config)
    proxy_handler = ProxyHandler(db, config)

    class AppRequestHandler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        # Suppress default server version header
        server_version = "LlamaReservationProxy/1.0"
        sys_version = ""

        def log_message(self, format: str, *args) -> None:
            # Custom log format to prevent logging sensitive details or queries
            pass

        def send_error_json(self, status: int, code: str, message: str) -> None:
            payload = json.dumps({"error": {"code": code, "message": message}}, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def send_html(self, status: int, html_str: str, extra_headers: Optional[Dict[str, str]] = None) -> None:
            payload = html_str.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            if extra_headers:
                for k, v in extra_headers.items():
                    self.send_header(k, v)
            self.end_headers()
            self.wfile.write(payload)

        def redirect(self, location: str, headers: Optional[Dict[str, str]] = None) -> None:
            self.send_response(303)
            self.send_header("Location", location)
            if headers:
                for k, v in headers.items():
                    self.send_header(k, v)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def do_GET(self) -> None:
            path = self.path.split("?")[0]

            if path == "/":
                self.redirect("/app/")
                return

            if path == "/health":
                payload = b'{"status":"ok"}'
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
                return

            if path == "/v1/models":
                self.send_error_json(404, "not_found", "模型列表接口不可用")
                return

            if path.startswith("/app"):
                web_handler.handle_get(self)
                return

            # Any other path
            self.send_error_json(404, "not_found", "未找到该接口")

        def do_POST(self) -> None:
            path = self.path.split("?")[0]

            # Read body
            content_len_header = self.headers.get("Content-Length")
            body_bytes = b""
            if content_len_header:
                try:
                    content_len = int(content_len_header)
                    body_bytes = self.rfile.read(content_len)
                except ValueError:
                    self.send_error_json(400, "invalid_content_length", "请求头 Content-Length 格式错误")
                    return

            if path.startswith("/app"):
                web_handler.handle_post(self, body_bytes)
                return

            if proxy_handler.is_allowed_path(path):
                proxy_handler.forward_request(self, body_bytes)
                return

            if path == "/v1/models":
                self.send_error_json(404, "not_found", "模型列表接口不可用")
                return

            self.send_error_json(404, "not_found", "未受支持的推理入口")

        def do_PUT(self) -> None:
            self.send_error_json(405, "method_not_allowed", "方法不被允许")

        def do_DELETE(self) -> None:
            self.send_error_json(405, "method_not_allowed", "方法不被允许")

    return AppRequestHandler

def create_server(config: AppConfig) -> ThreadedHTTPServer:
    db = Database(config.db_path)
    handler_class = make_request_handler(db, config)
    server = ThreadedHTTPServer((config.host, config.port), handler_class)
    return server
