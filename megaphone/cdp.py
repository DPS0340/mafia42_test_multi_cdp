"""CDP(Chrome DevTools Protocol)로 인증 토큰과 채널 서버 목록 캡처.

게임이 채널 서버로 보내는 인증 패킷(MSG_AUTH)과 channel_ko.php 응답을
원격 디버깅 세션에서 가로채 가져온다. 채널 전환 1회가 필요하다.
"""

from __future__ import annotations

import base64
import json
import logging
import urllib.request
from typing import Any, Optional

import websocket

from .config import CDP_PORT, CAPTURE_TIMEOUT
from .protocol import MSG_AUTH, parse_packet

logger = logging.getLogger("megaphone")


def connect_cdp() -> Optional[str]:
    """Connect to CDP and return the page target's WebSocket debugger URL.

    Returns None on failure.
    """
    try:
        url = f"http://127.0.0.1:{CDP_PORT}/json"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=3) as resp:
            targets = json.loads(resp.read().decode())
    except Exception as exc:
        logger.warning("CDP connection failed: %s", exc)
        return None
    for t in targets:
        if t.get("type") == "page" and t.get("webSocketDebuggerUrl"):
            return t["webSocketDebuggerUrl"]
    return None


def _looks_like_channel_list(body: str) -> Optional[list[dict[str, Any]]]:
    """Check if a channel_ko.php response body matches the expected format."""
    if not body or "channel_id" not in body:
        return None
    try:
        data = json.loads(body)
    except Exception:
        return None
    if isinstance(data, list) and data and "channel_id" in data[0] and "host" in data[0]:
        return data
    return None


def extract_token_and_channels_cdp() -> tuple[Optional[str], Optional[list[dict]], Optional[dict]]:
    """Capture auth token + channel server list + channel API info via CDP.

    Returns (token, channels, channel_api). channel_api is {'url': str, 'headers': dict}
    or None — used for population polling.
    """
    logger.info("Connecting to CDP (port %d)...", CDP_PORT)
    cdp_url = connect_cdp()
    if not cdp_url:
        logger.error(
            "CDP connection failed. Ensure Mafia42 is running with "
            "--remote-debugging-port=%d",
            CDP_PORT,
        )
        return None, None, None

    cdp = websocket.create_connection(cdp_url, timeout=10)
    cdp.send(json.dumps({"id": 1, "method": "Network.enable"}))

    logger.info(
        "Please switch to any channel in the game... (waiting for token + channel info)"
    )

    token: Optional[str] = None
    channels: Optional[list[dict]] = None
    channel_api: Optional[dict] = None  # {'url': ..., 'headers': ...} — 인원 폴링용
    pending_body_requests: set[int] = set()
    cdp_id: int = 100

    try:
        cdp.settimeout(CAPTURE_TIMEOUT)
        while True:
            msg = json.loads(cdp.recv())
            method = msg.get("method", "")
            params = msg.get("params", {})

            # 채널 API 요청의 URL + 헤더 캡처 (나중에 인원수 직접 폴링용)
            if method == "Network.requestWillBeSent":
                req = params.get("request", {})
                if "channel_ko.php" in req.get("url", ""):
                    channel_api = {"url": req["url"], "headers": req.get("headers", {})}

            # Auth token capture: intercept WebSocket frames with MSG_AUTH.
            elif method == "Network.webSocketFrameSent":
                payload_b64 = params.get("response", {}).get("payloadData", "")
                if payload_b64 and len(payload_b64) > 20:
                    try:
                        raw = base64.b64decode(payload_b64)
                        if len(raw) >= 12:
                            _, msg_type, payload = parse_packet(raw)
                            if msg_type == MSG_AUTH and len(payload) > 10:
                                token = payload.decode("ascii", errors="replace")
                                logger.info(
                                    "Token captured! (length: %d)", len(token)
                                )
                    except Exception:
                        pass  # Not an auth frame — ignore.

            # Channel API response arrived → request body.
            elif method == "Network.responseReceived":
                url = params.get("response", {}).get("url", "")
                if "channel_ko.php" in url:
                    cdp_id += 1
                    cdp.send(
                        json.dumps(
                            {
                                "id": cdp_id,
                                "method": "Network.getResponseBody",
                                "params": {"requestId": params.get("requestId", "")},
                            }
                        )
                    )
                    pending_body_requests.add(cdp_id)

            # getResponseBody response → parse channel list.
            elif msg.get("id") in pending_body_requests:
                body = msg.get("result", {}).get("body", "")
                parsed = _looks_like_channel_list(body)
                if parsed is not None:
                    channels = parsed
                    logger.info("Channel server info captured! (%d servers)", len(channels))

            if token and channels:
                break

    except websocket.WebSocketTimeoutException:
        logger.warning(
            "Timeout: no channel switch detected within %d seconds.", CAPTURE_TIMEOUT
        )
    finally:
        cdp.close()

    return token, channels, channel_api
