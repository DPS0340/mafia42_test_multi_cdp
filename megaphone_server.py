"""Mafia42 전체 채널 확성기 모니터 + 웹 인터페이스 — 진입점.

사용법:
  1. Mafia42를 디버깅 모드로 실행:
     "Mafia42.exe" --remote-debugging-port=9222 --remote-allow-origins=*
  2. 게임에 로그인
  3. python megaphone_server.py
  4. 게임에서 아무 채널로 이동 (인증 토큰 캡처)
  5. 브라우저에서 http://localhost:8080 접속

구현은 megaphone/ 패키지에 모듈별로 나뉘어 있다.
"""

from __future__ import annotations

import logging
import sys

logger = logging.getLogger("megaphone")


def setup_logging() -> None:
    """Configure structured logging to stdout."""
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        logging.Formatter("[%(asctime)s] %(levelname)s %(name)s: %(message)s", "%H:%M:%S")
    )
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)


def main() -> None:
    setup_logging()

    try:
        import websocket  # noqa: F401  (verify dependency exists)
    except ImportError:
        print("[!] websocket-client required: pip install -r requirements.txt")
        raise SystemExit(1)

    from megaphone.app import main as package_main

    package_main()


if __name__ == "__main__":
    main()
