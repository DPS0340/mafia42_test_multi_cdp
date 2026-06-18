"""오케스트레이션: 웹 서버 기동 → 토큰/채널 캡처 → 채널 연결.

마피아42 서버는 토큰(=계정) 1개당 채널 세션 1개만 허용한다(여러 채널 동시 접속
시 서로 강퇴됨). 전체 확성기는 어느 채널에 붙어 있어도 다 수신되므로, 단일 계정
모드에서는 핵심 채널 1개만 잡아 전체 확성기 + 그 채널 확성기를 받는다. 채널별
확성기를 더 받으려면 tokens.json 에 채널별 계정 토큰을 넣는다(megaphone/tokens.py 참고).

사용법:
  python megaphone_server.py                # tokens.json 없으면 핵심 채널(CORE_CHANNEL)만,
                                            # 있으면 tokens.json 에 등록된 채널 전체
  python megaphone_server.py --channels 0   # 초보 채널만 접속
  python megaphone_server.py --channels 0,1 # 초보+1채널 (단일 계정이면 서로 강퇴 주의)
"""

from __future__ import annotations

import logging
import signal
import sys
import threading
import time
from typing import Any, Optional

from .cdp import extract_token_and_channels_cdp
from .channel import ChannelConnection
from .config import (
    CORE_CHANNEL,
    EXCLUDED_CHANNELS,
    WS_PORT,
    apply_config_defaults,
    channel_name,
    load_config,
    save_config,
)
from .population import PopulationPoller, populations_from_list
from .store import store
from .tokens import load_token_overrides, resolve_channel_tokens
from .webserver import start_web_server

logger = logging.getLogger("megaphone")


def parse_selected_channels(argv: list[str]) -> Optional[set[int]]:
    """Parse --channels 0,1,2 → {0, 1, 2}. None means all channels."""
    if "--channels" not in argv:
        return None
    i = argv.index("--channels")
    if i + 1 >= len(argv):
        return None

    raw_value = argv[i + 1].strip()
    if not raw_value:
        return None

    selected = {
        int(part)
        for part in raw_value.split(",")
        if part.strip().isdigit()
    }
    return selected or None


def decide_selected_channels(
    selected: Optional[set[int]],
    available_ids: list[int],
    overrides: dict[int, str],
) -> set[int]:
    """--channels 미지정 시 접속할 채널을 결정한다.

    - 명시적으로 지정했으면 그대로 사용.
    - tokens.json(멀티 계정)이 있으면 거기 등록된 채널 전체.
    - 둘 다 없으면(단일 계정) 핵심 채널 1개만.
    """
    if selected is not None:
        return selected
    if overrides:
        return set(overrides.keys())
    return {CORE_CHANNEL} if CORE_CHANNEL in available_ids else {available_ids[0]}


def main() -> None:
    """Entry point: web server → capture → connect channels."""
    print("=" * 60)
    print("  Mafia42 Megaphone Monitor")
    print("=" * 60)

    # Load config file (if present).
    cfg = load_config()
    settings = apply_config_defaults(cfg)

    selected = parse_selected_channels(sys.argv)

    # Config fallback: use saved selected_channels when no CLI override.
    if selected is None and settings.get("selected_channels"):
        selected = {int(c) for c in settings["selected_channels"]}

    # 채널별 토큰 override (멀티 계정 모드). 단일 계정이면 보통 비어 있음.
    overrides = load_token_overrides()

    # 1. Start web server FIRST so the UI is immediately accessible.
    print(f"\n[1] Web server: http://localhost:{settings['web_port']}")
    http_server = start_web_server(settings["web_port"])

    # 2. Capture auth token + channel server info via CDP (runs in background).
    saved_token = cfg.get("token", "").strip()
    saved_channels = cfg.get("channels")

    token: Optional[str] = None
    channels: Optional[list[dict[str, Any]]] = None
    channel_api: Optional[dict] = None

    if saved_token and saved_channels:
        # Use saved config — no CDP needed.
        token = saved_token
        channels = saved_channels
        print("\n[2] Using saved token + channel info from config (no CDP needed).")
        print(f"    Token length: {len(token)}")
        print(f"    Channels: {len(channels)}")
    else:
        # CDP capture.
        print("\n[2] Capturing auth token + channel server info via CDP...")
        print("    Switch to any channel in the game to capture...")

        def _cdp_capture():
            nonlocal token, channels, channel_api
            t, ch, api = extract_token_and_channels_cdp()
            if t:
                token = t
            if ch:
                channels = ch
            if api:
                channel_api = api

        cdp_thread = threading.Thread(target=_cdp_capture, daemon=True)
        cdp_thread.start()

        # Wait for CDP capture with timeout.
        cdp_thread.join(timeout=180)

        if not channels:
            # Fallback: use saved channels from config.
            if saved_channels:
                channels = saved_channels
                print(f"[*] CDP channel capture timeout. Using saved channels ({len(channels)} servers)")
            else:
                print("[!] Could not retrieve channel server info.")
                print("    A channel switch in the game is required to capture channel_ko.php.")
                print("    Web server still running at http://localhost:{settings['web_port']}")
                # Keep web server alive — don't exit.
                try:
                    while True:
                        time.sleep(1)
                except KeyboardInterrupt:
                    print("\n[*] Shutting down...")
                    http_server.shutdown()
                return

        assert channels is not None

        if token is None and not overrides:
            if saved_token:
                token = saved_token
                print(f"\n[*] CDP token capture timeout. Using saved token (length: {len(token)})")
            else:
                print("\n[!] Token not captured yet. Web server is running.")
                print("    Log in to the game and switch channels to capture the token.")
                # Keep web server alive — poll for token.
                for _ in range(360):  # 30 minutes
                    time.sleep(5)
                    if token:
                        break
                if not token:
                    print("[!] Token capture timed out (30 min). Shutting down.")
                    http_server.shutdown()
                    return

        # Save token + channels to config for future runs without CDP.
        if token:
            cfg["token"] = token
        cfg["channels"] = channels
        save_config(cfg)

    # Normalize channel_id to int.
    for ch in channels:
        ch["channel_id"] = int(ch["channel_id"])

    # 감시 제외 채널(예: 비밀 — 관리자 채널 추정)은 접속·인원 표시 대상에서 완전 제거.
    excluded_present = [ch["channel_id"] for ch in channels
                        if ch["channel_id"] in EXCLUDED_CHANNELS]
    if excluded_present:
        logger.info("Excluding channels from monitoring: %s", sorted(set(excluded_present)))
    channels = [ch for ch in channels if ch["channel_id"] not in EXCLUDED_CHANNELS]

    available_ids = [ch["channel_id"] for ch in channels]

    # 전 채널 초기 인원수 시드 (접속하지 않는 채널도 인원은 표시한다)
    store.set_populations(populations_from_list(channels))

    # 접속할 채널 결정 (단일 계정이면 핵심 채널 1개, 멀티 계정이면 tokens.json 채널들)
    selected = decide_selected_channels(selected, available_ids, overrides)
    channels = [ch for ch in channels if ch["channel_id"] in selected]
    if not channels:
        print(f"[!] Selected channels {sorted(selected)} not in list.")
        return

    # 채널별 사용할 토큰 해석 (override 우선, 없으면 캡처 토큰)
    channel_tokens = resolve_channel_tokens(
        [ch["channel_id"] for ch in channels], token, overrides
    )

    # 토큰이 없는 채널 처리: 캡처도 실패하고 override도 없으면 수동 입력으로 폴백
    missing = [ch["channel_id"] for ch in channels
               if ch["channel_id"] not in channel_tokens]
    if missing:
        print(f"\n[!] Token missing for channels: {[channel_name(c) for c in missing]}")
        # Don't block on input in headless mode — just skip.
        channels = [ch for ch in channels if ch["channel_id"] in channel_tokens]
        if not channels:
            print("[!] No channels with usable token. Web server still running.")
            try:
                while True:
                    time.sleep(1)
            except KeyboardInterrupt:
                print("\n[*] Shutting down...")
                http_server.shutdown()
            return

    print(f"\n    Connecting to {len(channels)} channel(s):")
    for ch in channels:
        cid = ch["channel_id"]
        src = "tokens.json" if cid in overrides else "CDP"
        print(
            f"    - {channel_name(cid)} (id={cid}) → {ch['host']}:{WS_PORT}  "
            f"users: {ch.get('user_count', '?')}  [token: {src}]"
        )

    # 3. Population polling (channel_ko.php periodic refresh).
    if channel_api and channel_api.get("url"):
        PopulationPoller(channel_api["url"], channel_api.get("headers")).start()
        print("    Population polling started (channel_ko.php)")
    else:
        print("    [Population] No channel API URL captured - showing initial counts only")

    # 4. Connect to channels.
    print("\n[4] Starting channel connections...")
    connections: list[ChannelConnection] = []
    for ch in channels:
        conn = ChannelConnection(ch["channel_id"], ch["host"], channel_tokens[ch["channel_id"]])
        conn.start()
        connections.append(conn)
        time.sleep(0.5)  # Stagger connections to avoid server overload.

    print(f"\n[*] Monitoring started! Visit http://localhost:{settings['web_port']}")
    print("[*] Press Ctrl+C to exit\n")

    # Graceful shutdown handler.
    def shutdown_handler(signum: int, frame: Any) -> None:  # noqa: ARG001
        logger.info("Shutdown signal received (signal %d)", signum)
        for c in connections:
            c.running = False
        store.flush()  # 종료 전 미저장 메시지 디스크에 반영

    signal.signal(signal.SIGINT, shutdown_handler)
    signal.signal(signal.SIGTERM, shutdown_handler)

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n[*] Shutting down...")
        for c in connections:
            c.running = False
        store.flush()  # 종료 전 미저장 메시지 디스크에 반영
        http_server.shutdown()
