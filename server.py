"""水库防洪调度台 HTTP 服务（仅标准库，监听 8000）。"""

from __future__ import annotations

import json
import os
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

from core import data_loader
from core.engine import simulate
from core.ledger import Ledger, LedgerError
from core.selfcheck import run_selfcheck

HOST = "0.0.0.0"  # 8000 若已被 127.0.0.1 上的其他程序占用，仍可经本机 LAN IP 访问
PORT = 8000
STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")

RAW = data_loader.load()
LEDGER = Ledger(RAW)

CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
}


class Handler(BaseHTTPRequestHandler):
    server_version = "ReservoirDispatch/1.0"

    def log_message(self, fmt, *args):
        print("[http] " + (fmt % args))

    def _send_json(self, payload, status=HTTPStatus.OK):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self):
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise _HttpError(HTTPStatus.BAD_REQUEST, f"请求体不是合法JSON：{exc}")

    def do_GET(self):
        try:
            path = urlparse(self.path).path
            if path == "/api/overview":
                self._send_json(_overview())
            elif path == "/api/selfcheck":
                self._send_json(run_selfcheck(RAW))
            elif path == "/api/ledger":
                self._send_json(LEDGER.snapshot())
            elif path.startswith("/api/ledger/"):
                reservoir_id = path.rsplit("/", 1)[-1]
                self._send_json(LEDGER.reservoir(reservoir_id))
            elif path == "/api/simulate":
                self._send_json(_simulate_from_query(urlparse(self.path).query))
            else:
                self._serve_static(path)
        except LedgerError as exc:
            self._send_json({"error": exc.code, "message": str(exc),
                             "current": exc.current}, HTTPStatus.NOT_FOUND)
        except _HttpError as exc:
            self._send_json({"error": "bad_request", "message": exc.message}, exc.status)
        except Exception as exc:  # noqa: BLE001 - 顶层兜底，服务不能崩
            self._send_json({"error": "server_error", "message": str(exc)},
                            HTTPStatus.INTERNAL_SERVER_ERROR)

    def do_POST(self):
        try:
            path = urlparse(self.path).path
            payload = self._read_json()
            if path == "/api/simulate":
                self._send_json(_simulate_payload(payload))
            elif path.startswith("/api/ledger/") and path.endswith("/change"):
                reservoir_id = path.split("/")[3]
                try:
                    result = LEDGER.change(
                        reservoir_id,
                        payload.get("operator", "匿名值班员"),
                        int(payload.get("base_version", 0)),
                        payload.get("changes", []),
                    )
                except LedgerError as exc:
                    status = (HTTPStatus.CONFLICT if exc.code == "version_conflict"
                              else HTTPStatus.BAD_REQUEST)
                    self._send_json({"error": exc.code, "message": str(exc),
                                     "current": exc.current}, status)
                    return
                self._send_json(result)
            elif path.startswith("/api/ledger/") and path.endswith("/reset"):
                reservoir_id = path.split("/")[3]
                self._send_json(LEDGER.reset(reservoir_id))
            else:
                raise _HttpError(HTTPStatus.NOT_FOUND, f"未知接口：{path}")
        except _HttpError as exc:
            self._send_json({"error": "bad_request", "message": exc.message}, exc.status)
        except Exception as exc:  # noqa: BLE001
            self._send_json({"error": "server_error", "message": str(exc)},
                            HTTPStatus.INTERNAL_SERVER_ERROR)

    def _serve_static(self, path):
        if path == "/":
            path = "/index.html"
        relative = path.lstrip("/").replace("\\", "/")
        file_path = os.path.normpath(os.path.join(STATIC_DIR, relative))
        if not file_path.startswith(STATIC_DIR + os.sep) or not os.path.isfile(file_path):
            self._send_json({"error": "not_found", "message": path}, HTTPStatus.NOT_FOUND)
            return
        ext = os.path.splitext(file_path)[1].lower()
        with open(file_path, "rb") as fh:
            body = fh.read()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", CONTENT_TYPES.get(ext, "application/octet-stream"))
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class _HttpError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status
        self.message = message


def _simulate_payload(payload):
    reservoir_id = payload.get("reservoir_id", "QFS")
    strategy = payload.get("strategy", "A")
    safety_factor = float(payload.get("safety_factor", 1.0))
    overrides = payload.get("overrides") or []
    return simulate(RAW, reservoir_id, strategy, overrides=overrides,
                    safety_factor=safety_factor)


def _simulate_from_query(query):
    from urllib.parse import parse_qs
    params = parse_qs(query)
    reservoir_id = params.get("reservoir", ["QFS"])[0]
    strategy = params.get("strategy", ["A"])[0]
    safety_factor = float(params.get("safety", ["1.0"])[0])
    return simulate(RAW, reservoir_id, strategy, safety_factor=safety_factor)


def _overview():
    reservoirs = []
    for reservoir in RAW["reservoirs"]:
        runs = {
            strategy: simulate(RAW, reservoir["id"], strategy)["summary"]
            for strategy in ("A", "B")
        }
        reservoirs.append({
            "id": reservoir["id"],
            "name": reservoir["name"],
            "flood_limit_level": reservoir["flood_limit_level"],
            "current_level": reservoir["current_level"],
            "gates": [
                {"uid": g["uid"], "code": g["code"], "name": g["name"],
                 "max_gear": g["max_gear"]}
                for g in reservoir["gates"]
            ],
            "curve": reservoir["curve"],
            "issues": reservoir["issues"],
            "summary_a": runs["A"],
            "summary_b": runs["B"],
        })
    return {
        "event": RAW["event"],
        "hydrograph": RAW["hydrograph"],
        "reaches": RAW["reaches"],
        "reservoirs": reservoirs,
        "data_issues": RAW["data_issues"],
    }


def main():
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    lan_ip = _lan_ip()
    print(f"水库防洪调度台已启动：http://{lan_ip}:{PORT}")
    print("（若本机 127.0.0.1:8000 被其他程序占用，请使用上面的 LAN IP）")
    print(f"台账脏数据 {len(RAW['data_issues'])} 处已在界面标注")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n服务停止")
    finally:
        server.server_close()


def _lan_ip():
    import socket
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.connect(("8.8.8.8", 80))
        ip = sock.getsockname()[0]
        sock.close()
        return ip
    except OSError:
        return "127.0.0.1"


if __name__ == "__main__":
    main()
