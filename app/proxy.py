import sys
import time
import uuid
import logging
import json
import requests
from typing import Dict, Any, Optional, Tuple
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from app.config import AppConfig
from app.database import Database
from app.time_utils import TimeProvider
from app.models import find_user_by_api_key, get_active_maintenance, add_audit_log
from app.reservation_engine import check_active_reservation_for_user

logger = logging.getLogger("proxy")

# Strip hop-by-hop headers according to RFC 7230
HOP_BY_HOP_HEADERS = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade"
}

ALLOWED_PROXY_PATHS = {
    "/v1/chat/completions",
    "/v1/completions",
    "/completion"
}

class ProxyHandler:
    def __init__(self, db: Database, config: AppConfig):
        self.db = db
        self.config = config
        self.booking_models = {model["model_name"]: model for model in config.get_booking_models()}
        
        # Create session with ZERO retries to avoid duplicating inference requests
        # and trust_env=False to avoid proxying local/internal calls through environment proxies
        self.session = requests.Session()
        self.session.trust_env = False
        adapter = HTTPAdapter(max_retries=Retry(total=0, connect=0, read=0))
        self.session.mount("http://", adapter)
        self.session.mount("https://", adapter)

    def is_allowed_path(self, path: str) -> bool:
        return path in ALLOWED_PROXY_PATHS

    def authenticate_and_authorize(self, auth_header: Optional[str], path: str, model_id: str) -> Tuple[Optional[int], Optional[Dict[str, Any]]]:
        """
        Returns (user_id, error_dict)
        error_dict: {"status": int, "code": str, "message": str} or None if authorized.
        """
        now = TimeProvider.now()

        # 1. Check API Key
        if not auth_header or not auth_header.startswith("Bearer "):
            return None, {"status": 401, "code": "invalid_api_key", "message": "缺少或无效的 Authorization Bearer 凭据"}

        raw_key = auth_header[7:].strip()
        if not raw_key:
            return None, {"status": 401, "code": "invalid_api_key", "message": "API Key 不能为空"}

        try:
            with self.db.connection() as conn:
                user_key_row = find_user_by_api_key(conn, raw_key)
                if not user_key_row:
                    return None, {"status": 401, "code": "invalid_api_key", "message": "无效的 API Key"}

                user_id = user_key_row["user_id"]
                is_active = user_key_row["is_active"]

                if not is_active:
                    return None, {"status": 401, "code": "invalid_api_key", "message": "账号已停用"}

                # 2. Check Maintenance
                active_m = get_active_maintenance(conn)
                if active_m is not None:
                    return user_id, {"status": 503, "code": "system_maintenance", "message": "系统维护"}

                # 3. Check Active Reservation
                reservation = check_active_reservation_for_user(conn, user_id, now, model_id=model_id)
                if reservation is None:
                    return user_id, {"status": 403, "code": "reservation_required", "message": "当前时段没有该模型的有效预约"}

                # Admission granted!
                return user_id, None
        except Exception as e:
            logger.error(f"Database error during authorization: {type(e).__name__}")
            # Fail closed: reject request rather than bypass
            return None, {"status": 500, "code": "database_unavailable", "message": "数据库异常，拒绝放行"}

    def iter_response_chunks(self, resp: requests.Response):
        """
        Yields response chunks respecting Transfer-Encoding and Content-Length.
        - Chunked: reads chunk by chunk via resp.raw.read_chunked() (zero buffering for SSE).
        - Content-Length: reads strictly up to content_length bytes without reading socket to EOF,
          preserving HTTP/1.1 keep-alive connections.
        - Neither (close-delimited): falls back to unbuffered read1 or iter_content until EOF.
        """
        if getattr(resp.raw, "chunked", False):
            try:
                for chunk in resp.raw.read_chunked():
                    if chunk:
                        yield chunk
                return
            except Exception:
                raise

        cl_header = resp.headers.get("content-length")
        if cl_header is not None:
            try:
                total_len = int(cl_header)
            except ValueError:
                total_len = None

            if total_len is not None:
                # Read strictly total_len bytes to preserve keep-alive
                remaining = total_len
                while remaining > 0:
                    read_size = min(4096, remaining)
                    chunk = resp.raw.read(read_size)
                    if not chunk:
                        break
                    remaining -= len(chunk)
                    yield chunk
                return

        # Upstream has neither chunked nor content-length (close-delimited stream)
        fp = getattr(getattr(resp.raw, "_fp", None), "fp", None)
        if fp and hasattr(fp, "read1"):
            while True:
                chunk = fp.read1(4096)
                if not chunk:
                    break
                yield chunk
        else:
            for chunk in resp.iter_content(chunk_size=1):
                if chunk:
                    yield chunk

    def forward_request(self, http_handler: Any, body_bytes: bytes) -> None:
        req_id = str(uuid.uuid4())
        start_time = time.time()
        path = http_handler.path.split("?")[0]

        # Explicitly check /v1/models: must return 404 without calling backend
        if path == "/v1/models":
            http_handler.send_error_json(404, "not_found", "模型列表接口不可用")
            return

        if not self.is_allowed_path(path):
            http_handler.send_error_json(404, "not_found", "未受支持的推理入口")
            return

        # Resolve the model before checking its reservation. Re-encode the parsed
        # object so authorization and the gateway see the same model value.
        try:
            payload = json.loads(body_bytes)
        except (ValueError, UnicodeDecodeError):
            http_handler.send_error_json(400, "invalid_request", "请求体必须为有效 JSON 对象")
            return
        if not isinstance(payload, dict):
            http_handler.send_error_json(400, "invalid_request", "请求体必须为 JSON 对象")
            return
        if "model" not in payload and len(self.booking_models) == 1:
            payload["model"] = next(iter(self.booking_models))
        model_name = payload.get("model")
        if not isinstance(model_name, str) or model_name not in self.booking_models:
            http_handler.send_error_json(400, "invalid_model", "请在 model 字段填写已配置的模型名称")
            return
        model_id = self.booking_models[model_name]["id"]
        try:
            body_bytes = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
        except (ValueError, UnicodeError):
            http_handler.send_error_json(400, "invalid_request", "请求体包含无效的 JSON 值")
            return

        auth_header = http_handler.headers.get("Authorization")
        user_id, auth_err = self.authenticate_and_authorize(auth_header, path, model_id=model_id)

        if auth_err:
            elapsed = time.time() - start_time
            logger.info(f"REQ {req_id} path={path} user_id={user_id} status={auth_err['status']} err={auth_err['code']} time={elapsed:.3f}s")
            http_handler.send_error_json(auth_err["status"], auth_err["code"], auth_err["message"])
            return

        # Prepare backend headers
        backend_headers = {}
        for k, v in http_handler.headers.items():
            k_lower = k.lower()
            if k_lower in HOP_BY_HOP_HEADERS:
                continue
            if k_lower in ("authorization", "cookie", "host", "x-forwarded-for", "x-forwarded-proto", "content-length", "content-type"):
                continue
            backend_headers[k] = v
        backend_headers["Content-Type"] = "application/json"

        if self.config.backend_key:
            backend_headers["Authorization"] = f"Bearer {self.config.backend_key}"

        backend_url = f"{self.config.backend_url.rstrip('/')}{path}"
        if "?" in http_handler.path:
            backend_url += "?" + http_handler.path.split("?", 1)[1]

        timeout_setting = (self.config.connect_timeout, self.config.read_timeout)
        resp = None
        response_started = False
        try:
            resp = self.session.post(
                backend_url,
                data=body_bytes,
                headers=backend_headers,
                stream=True,
                timeout=timeout_setting
            )

            # Check if upstream response has Content-Length or is streaming/chunked
            upstream_has_cl = "content-length" in (k.lower() for k in resp.headers)
            use_chunked_downstream = (not upstream_has_cl) and (getattr(http_handler, "request_version", "") != "HTTP/1.0")

            # Send response status and headers
            http_handler.send_response(resp.status_code)
            for k, v in resp.headers.items():
                k_lower = k.lower()
                if k_lower in HOP_BY_HOP_HEADERS:
                    continue
                http_handler.send_header(k, v)

            if use_chunked_downstream:
                http_handler.send_header("Transfer-Encoding", "chunked")
            elif not upstream_has_cl:
                http_handler.send_header("Connection", "close")
                http_handler.close_connection = True

            http_handler.end_headers()
            response_started = True

            # Stream body chunks to client immediately as they arrive
            for chunk in self.iter_response_chunks(resp):
                if chunk:
                    try:
                        if use_chunked_downstream:
                            http_handler.wfile.write(f"{len(chunk):X}\r\n".encode("ascii") + chunk + b"\r\n")
                        else:
                            http_handler.wfile.write(chunk)
                        http_handler.wfile.flush()
                    except (BrokenPipeError, ConnectionResetError):
                        logger.info(f"REQ {req_id} client disconnected")
                        break

            if use_chunked_downstream:
                try:
                    http_handler.wfile.write(b"0\r\n\r\n")
                    http_handler.wfile.flush()
                except Exception:
                    pass

            elapsed = time.time() - start_time
            logger.info(f"REQ {req_id} path={path} user_id={user_id} backend_status={resp.status_code} time={elapsed:.3f}s")

        except requests.exceptions.ConnectTimeout:
            elapsed = time.time() - start_time
            logger.warning(f"REQ {req_id} backend connect timeout path={path} user_id={user_id} time={elapsed:.3f}s")
            if not response_started:
                http_handler.send_error_json(504, "gateway_timeout", "上游模型服务连接超时")
            else:
                http_handler.close_connection = True
                try:
                    http_handler.connection.close()
                except Exception:
                    pass
        except requests.exceptions.ReadTimeout:
            elapsed = time.time() - start_time
            logger.warning(f"REQ {req_id} backend read timeout path={path} user_id={user_id} stream_started={response_started} time={elapsed:.3f}s")
            if not response_started:
                http_handler.send_error_json(504, "gateway_timeout", "上游模型服务响应超时")
            else:
                http_handler.close_connection = True
                try:
                    http_handler.connection.close()
                except Exception:
                    pass
        except requests.exceptions.ConnectionError:
            elapsed = time.time() - start_time
            logger.warning(f"REQ {req_id} backend connection error path={path} user_id={user_id} stream_started={response_started} time={elapsed:.3f}s")
            if not response_started:
                http_handler.send_error_json(502, "bad_gateway", "无法连接上游模型服务")
            else:
                http_handler.close_connection = True
                try:
                    http_handler.connection.close()
                except Exception:
                    pass
        except Exception as e:
            elapsed = time.time() - start_time
            logger.error(f"REQ {req_id} proxy error: {type(e).__name__} stream_started={response_started} time={elapsed:.3f}s")
            if not response_started:
                http_handler.send_error_json(502, "bad_gateway", "代理转发异常")
            else:
                http_handler.close_connection = True
                try:
                    http_handler.connection.close()
                except Exception:
                    pass
        finally:
            if resp is not None:
                resp.close()
