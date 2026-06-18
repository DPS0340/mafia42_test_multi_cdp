"""로컬 웹 인터페이스: 정적 페이지 + JSON API + SSE 스트림.

Endpoints:
    GET /          — Frontend HTML page.
    GET /events    — SSE stream (server-sent events).
    GET /api/messages  — Recent messages as JSON.
    GET /api/channels    — Channel statuses as JSON.
    GET /api/export?format=json|csv  — Export all messages.
"""

from __future__ import annotations

import csv
import io
import json
import logging
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from queue import Empty, Queue
from socketserver import ThreadingMixIn
from typing import Any
from urllib.parse import parse_qs

from .config import WEB_PORT, channel_name
from .store import store

logger = logging.getLogger("megaphone")

# Frontend HTML is in a separate file (web/index.html).
WEB_DIR = Path(__file__).parent / "web"


class WebHandler(BaseHTTPRequestHandler):
    """HTTP request handler for the web interface."""

    # Suppress per-request access logs (use structured logging instead).
    def log_message(self, format: str, *args: Any) -> None:
        pass

    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?")[0]  # Strip query string for routing.
        query = parse_qs(self.path.split("?", 1)[-1]) if "?" in self.path else {}

        if path == "/":
            self._serve_file("index.html", "text/html; charset=utf-8")
        elif path == "/test":
            self._serve_file("test_dummy.html", "text/html; charset=utf-8")
        elif path == "/megaphone-monitor.js":
            self._serve_file("megaphone-monitor.js", "text/javascript; charset=utf-8")
        elif path == "/events":
            self._serve_sse()
        elif path == "/api/messages":
            self._serve_json(store.get_recent(limit=100))
        elif path == "/api/messages/all":
            self._serve_json(store.get_all_messages())
        elif path == "/api/channels":
            statuses = store.get_status_snapshot()
            pops = store.get_population_snapshot()
            # 접속한 채널 + 인원만 잡힌 채널(미접속)까지 모두 노출
            ids = sorted(set(statuses) | set(pops))
            self._serve_json(
                [
                    {
                        "id": cid,
                        "name": channel_name(cid),
                        "status": statuses.get(cid),
                        "population": pops.get(cid),
                    }
                    for cid in ids
                ]
            )
        elif path == "/api/export":
            fmt = query.get("format", ["json"])[0]
            self._serve_export(fmt)
        else:
            self.send_error(404)

    def _serve_bytes(self, body: bytes, content_type: str) -> None:
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _serve_file(self, filename: str, content_type: str) -> None:
        """Read file from disk on each request (no caching)."""
        filepath = WEB_DIR / filename
        try:
            body = filepath.read_text(encoding="utf-8").encode("utf-8")
            self._serve_bytes(body, content_type)
        except FileNotFoundError:
            self.send_error(404)

    def _serve_json(self, data: list[dict]) -> None:
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self._serve_bytes(body, "application/json; charset=utf-8")

    def _serve_sse(self) -> None:
        """Serve an SSE (Server-Sent Events) stream.

        Uses a Queue for push-based delivery.  On client disconnect
        (OSError on write), the queue is cleaned up in the finally block.
        """
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()

        q: Queue[str] = Queue()
        store.add_queue(q)
        try:
            while True:
                try:
                    data = q.get(timeout=15)
                    self.wfile.write(f"data: {data}\n\n".encode("utf-8"))
                    self.wfile.flush()
                except Empty:
                    self.wfile.write(b": keepalive\n\n")
                    self.wfile.flush()
        except OSError:
            logger.debug("SSE client disconnected")
        except Exception:
            logger.exception("SSE stream error")
        finally:
            store.remove_queue(q)

    def _serve_export(self, fmt: str) -> None:
        """Export all messages as JSON or CSV."""
        all_msgs = store.get_all_messages()
        if fmt == "csv":
            self._export_csv(all_msgs)
        else:
            self._serve_json(all_msgs)

    def _export_csv(self, messages: list[dict]) -> None:
        """Send messages as a CSV attachment."""
        output = io.StringIO()
        fieldnames = ["time", "channel_id", "channel_name", "scope", "sender", "message"]
        writer = csv.DictWriter(output, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for msg in messages:
            writer.writerow(
                {
                    "time": msg.get("time", ""),
                    "channel_id": msg.get("channel_id", ""),
                    "channel_name": msg.get("channel_name", ""),
                    "scope": msg.get("scope", ""),
                    "sender": msg.get("sender", ""),
                    "message": msg.get("message", ""),
                }
            )
        body = output.getvalue().encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/csv; charset=utf-8")
        self.send_header(
            "Content-Disposition", 'attachment; filename="megaphone_export.csv"'
        )
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    """Multi-threaded HTTP server."""

    daemon_threads = True

    def handle_error(self, request, client_address):
        # 브라우저가 SSE/페이지 연결을 끊으면 소켓 읽기/쓰기에서
        # ConnectionAbortedError/Reset/BrokenPipe 가 발생한다. 정상적인
        # 연결 종료이므로 트레이스백 출력 없이 조용히 무시한다.
        import sys
        exc = sys.exc_info()[1]
        if isinstance(exc, (ConnectionResetError, ConnectionAbortedError, BrokenPipeError)):
            return
        super().handle_error(request, client_address)


def start_web_server(port: int = WEB_PORT) -> HTTPServer:
    """Start the web server in a background thread.  Returns the server object."""
    server = ThreadedHTTPServer(("0.0.0.0", port), WebHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    logger.info("Web server started on port %d", port)
    return server
