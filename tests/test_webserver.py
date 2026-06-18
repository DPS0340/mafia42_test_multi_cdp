"""Web server integration tests — HTTP endpoints, SSE, export.

Uses Hypothesis for response-shape invariants and property-based
validation of API contract.
"""
from __future__ import annotations

import http.client
import json
import socket

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from conftest import ch_id, sender, message_text
from megaphone.store import MessageStore
from megaphone.webserver import start_web_server


def _find_free_port() -> int:
    """Return a free TCP port on localhost."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        return s.getsockname()[1]


@pytest.fixture
def server():
    """Start a web server on a random port, yield it, then shutdown."""
    port = _find_free_port()
    srv = start_web_server(port)
    if not hasattr(srv, "shutdown"):
        srv.shutdown = lambda: None
    yield srv, port
    srv.shutdown()


class TestWebServer:
    """Integration tests for HTTP web server endpoints."""

    # ------------------------------------------------------------------
    # Static file serving
    # ------------------------------------------------------------------

    @pytest.mark.integration
    def test_root_returns_html(self, server):
        """GET / returns index.html with megaphone-monitor web component."""
        _, port = server
        conn = http.client.HTTPConnection("localhost", port, timeout=5)
        try:
            conn.request("GET", "/")
            resp = conn.getresponse()
            body = resp.read().decode("utf-8")
            assert resp.status == 200
            ct = resp.getheader("Content-Type", "")
            assert "text/html" in ct
            assert "megaphone-monitor" in body
        finally:
            conn.close()

    @pytest.mark.integration
    def test_test_page_returns_html(self, server):
        """GET /test returns test_dummy.html with mode='test'."""
        _, port = server
        conn = http.client.HTTPConnection("localhost", port, timeout=5)
        try:
            conn.request("GET", "/test")
            resp = conn.getresponse()
            body = resp.read().decode("utf-8")
            assert resp.status == 200
            assert "megaphone-monitor" in body
            assert 'mode="test"' in body
        finally:
            conn.close()

    @pytest.mark.integration
    def test_js_file_served(self, server):
        """GET /megaphone-monitor.js returns JavaScript."""
        _, port = server
        conn = http.client.HTTPConnection("localhost", port, timeout=5)
        try:
            conn.request("GET", "/megaphone-monitor.js")
            resp = conn.getresponse()
            body = resp.read().decode("utf-8")
            assert resp.status == 200
            ct = resp.getheader("Content-Type", "")
            assert "javascript" in ct
            assert "MegaphoneMonitor" in body
        finally:
            conn.close()

    @pytest.mark.integration
    def test_404_returns_not_found(self, server):
        """GET /nonexistent returns 404."""
        _, port = server
        conn = http.client.HTTPConnection("localhost", port, timeout=5)
        try:
            conn.request("GET", "/nonexistent")
            resp = conn.getresponse()
            assert resp.status == 404
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # API endpoints: response shape invariants
    # ------------------------------------------------------------------

    @pytest.mark.integration
    @given(extra=st.lists(st.builds(
        lambda c, s, m, mid: (c, {"sender": s, "message": m, "msg_id": mid}),
        c=ch_id(), s=sender(), m=message_text(), mid=st.integers(min_value=1, max_value=999999),
    ), min_size=1, max_size=5))
    @settings(max_examples=20, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
    def test_messages_endpoint_shape(self, extra, server):
        """INVARIANT: GET /api/messages returns a JSON list."""
        local_store = MessageStore()
        for cid, msg in extra:
            local_store.add(cid, msg)
        _, port = server
        conn = http.client.HTTPConnection("localhost", port, timeout=5)
        try:
            conn.request("GET", "/api/messages")
            resp = conn.getresponse()
            body = resp.read().decode("utf-8")
            assert resp.status == 200
            ct = resp.getheader("Content-Type", "")
            assert "json" in ct
            data = json.loads(body)
            assert isinstance(data, list)
        finally:
            conn.close()

    @pytest.mark.integration
    def test_channels_endpoint_shape(self, server):
        """INVARIANT: GET /api/channels returns a JSON list."""
        _, port = server
        conn = http.client.HTTPConnection("localhost", port, timeout=5)
        try:
            conn.request("GET", "/api/channels")
            resp = conn.getresponse()
            body = resp.read().decode("utf-8")
            assert resp.status == 200
            data = json.loads(body)
            assert isinstance(data, list)
            if len(data) > 0:
                for ch in data:
                    assert "id" in ch
                    assert "name" in ch
                    assert "status" in ch
        finally:
            conn.close()

    @pytest.mark.integration
    def test_all_messages_endpoint_shape(self, server):
        """INVARIANT: GET /api/messages/all returns a JSON list."""
        _, port = server
        conn = http.client.HTTPConnection("localhost", port, timeout=5)
        try:
            conn.request("GET", "/api/messages/all")
            resp = conn.getresponse()
            body = resp.read().decode("utf-8")
            assert resp.status == 200
            data = json.loads(body)
            assert isinstance(data, list)
        finally:
            conn.close()

    @pytest.mark.integration
    def test_export_json_shape(self, server):
        """INVARIANT: GET /api/export?format=json returns a JSON list."""
        _, port = server
        conn = http.client.HTTPConnection("localhost", port, timeout=5)
        try:
            conn.request("GET", "/api/export?format=json")
            resp = conn.getresponse()
            body = resp.read().decode("utf-8")
            assert resp.status == 200
            ct = resp.getheader("Content-Type", "")
            assert "json" in ct
            data = json.loads(body)
            assert isinstance(data, list)
        finally:
            conn.close()

    @pytest.mark.integration
    def test_export_csv_invariants(self, server):
        """INVARIANT: GET /api/export?format=csv returns valid CSV with header."""
        _, port = server
        conn = http.client.HTTPConnection("localhost", port, timeout=5)
        try:
            conn.request("GET", "/api/export?format=csv")
            resp = conn.getresponse()
            body = resp.read().decode("utf-8")
            assert resp.status == 200
            ct = resp.getheader("Content-Type", "")
            assert "csv" in ct
            lines = body.strip().split("\n")
            assert len(lines) >= 1
            header = lines[0]
            assert "sender" in header
            assert "message" in header
            assert "time" in header
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # SSE endpoint
    # ------------------------------------------------------------------

    @pytest.mark.integration
    def test_events_sse_endpoint(self, server):
        """INVARIANT: GET /events returns text/event-stream."""
        _, port = server
        conn = http.client.HTTPConnection("localhost", port, timeout=5)
        try:
            conn.request("GET", "/events")
            resp = conn.getresponse()
            assert resp.status == 200
            ct = resp.getheader("Content-Type", "")
            assert "text/event-stream" in ct
        finally:
            conn.close()
