# Mafia42 확성기 모니터

[![CI](https://github.com/mytrashcan/mafia42_test/actions/workflows/test.yml/badge.svg)](https://github.com/mytrashcan/mafia42_test/actions/workflows/test.yml)

마피아42 PC 클라이언트(Electron)에서 전 채널 확성기 메시지를 실시간으로 수집해
웹 페이지(`http://localhost:8080`)에 표시하는 도구입니다.

## 동작 방식

### CDP 패시브 모니터 (기본, `cdp_megaphone_monitor.py`)

게임 클라이언트의 WebSocket 트래픽을 CDP(`Network.webSocketFrameReceived`)로
**수동 관찰**하는 방식입니다. 게임이 이미 접속한 채널의 확성기 메시지를 그대로 읽습니다.

```
게임 클라이언트 ──WS──▶ 채널 서버
      │
      └──CDP(9222)──▶ cdp_megaphone_monitor.py ──SSE──▶ 브라우저
```

- **토큰 불필요** — 게임의 라이브 세션을 그대로 활용
- **세션 충돌 없음** — 수동 관찰이라 게임 세션에 영향 없음
- **토큰 만료 문제 없음** — 게임이 알아서 갱신하므로 모니터가 만료를 신경 쓸 필요 없음
- **단점** — 게임이 접속한 채널만 모니터링 가능 (채널 이동 시 해당 채널로 자동 전환)

### 다이렉트 WS 모드 (`megaphone_server.py`)

CDP로 인증 토큰(ya29)을 캡처한 뒤, 모니터가 **직접 채널 서버에 WebSocket 연결**하는
방식입니다. `tokens.json`으로 멀티 계정을 설정하면 여러 채널에 동시 접속 가능합니다.

```
CDP 캡처 ──▶ 토큰 획득 ──▶ 직접 WS 연결 ──▶ 채널 서버
                                              │
                              megaphone_server.py ──SSE──▶ 브라우저
```

- **멀티 채널/계정 지원** — 채널마다 다른 계정 토큰으로 동시 접속
- **단점** — ya29 토큰은 약 1시간 만료, 단독 갱신 불가
- **단점** — 같은 토큰으로 게임과 모니터가 동시에 접속하면 세션 충돌 발생

### 왜 두 방식이 있나?

마피아42 서버는 **토큰(=계정) 1개당 활성 세션을 1개만** 허용합니다. 직접 WS 방식은
토큰 만료와 세션 충돌这两个 근본적 문제가 있어서, CDP 패시브 모니터를 대안으로
도입했습니다. 현재 스케줄드 태스크(`Mafia42Megaphone`)는 CDP 패시브 모니터를
사용합니다.

| | CDP 패시브 모니터 | 다이렉트 WS |
|---|---|---|
| 토큰 필요 | ✗ | ✓ (ya29, ~1시간 만료) |
| 세션 충돌 | ✗ 없음 | ✓ 발생 가능 |
| 멀티 채널 | 게임 접속 채널만 | ✓ 계정별 접속 |
| 실행 복잡도 | 낮음 | 높음 |
| 현재 사용 | ✓ **기본** | 레거시 |

## 설치

```
pip install -r requirements.txt
```

## 사용법

### CDP 패시브 모니터 (기본)

1. 마피아42를 원격 디버깅 모드로 실행:

   ```powershell
   & "C:\Users\<사용자>\AppData\Local\Programs\Mafia42\Mafia42.exe" --remote-debugging-port=9222 --remote-allow-origins=*
   ```

   또는 바탕화면의 `mafia42_debug.bat`을 실행합니다.

2. 게임에 로그인하고 아무 채널로 이동합니다.

3. 모니터 실행:

   ```
   python cdp_megaphone_monitor.py
   ```

4. 브라우저에서 `http://localhost:8080` 접속.

게임에서 채널을 이동하면 모니터가 자동으로 따라갑니다. `Ctrl+C`로 종료합니다.

### 다이렉트 WS 모드

```
python megaphone_server.py                # 기본: 핵심 채널 1개만 (config의 CORE_CHANNEL)
python megaphone_server.py --channels 0   # 초보 채널만
python megaphone_server.py --channels 0,1 # 초보 + 1채널
```

처음 실행 시 CDP로 토큰과 채널 서버 정보를 캡처합니다. 캡처 후에는 게임 클라이언트를
**로비(채널 밖)**에 두는 것이 좋습니다. 같은 계정이 모니터링 중인 채널에 들어가 있으면
세션이 충돌해 연결이 끊길 수 있습니다.

### 멀티 계정 (다이렉트 WS 모드 전용)

채널마다 다른 계정을 사용하려면 프로젝트 루트에 `tokens.json`을 만듭니다
(`tokens.json.example` 참고):

```json
{
  "0": "TEST_TOKEN_CHANNEL_0",
  "1": "TEST_TOKEN_CHANNEL_1"
}
```

`tokens.json`이 있으면 `--channels` 없이 실행해도 등록된 채널들에 각 계정 토큰으로
동시 접속합니다.

> `tokens.json`은 인증 토큰(비밀정보)이라 `.gitignore`에 등록되어 커밋되지 않습니다.

## 프로젝트 구조

```
cdp_megaphone_monitor.py   ★ CDP 패시브 모니터 (현재 기본 진입점)
megaphone_server.py        다이렉트 WS 모드 진입점
start_megaphone.bat        Windows 스케줄드 태스크용 배치 파일
mafia42_debug.bat          게임 디버그 모드 실행 배치 파일 (바탕화면)
megaphone/
  config.py                포트·채널 이름·기본 핵심 채널(CORE_CHANNEL) 등 상수
  protocol.py              패킷 인코딩/디코딩 + 메시지 타입 상수
  store.py                 수신 메시지/SSE 구독자 보관 (스레드 안전)
  cdp.py                   CDP로 토큰·채널 목록·채널 API URL 캡처
  tokens.py                채널별 토큰 해석 (tokens.json override + CDP 캡처 폴백)
  population.py            채널별 접속 인원 폴링 (channel_ko.php 주기적 호출)
  channel.py               채널별 WebSocket 연결 스레드
  webserver.py             HTTP + SSE 웹 서버
  app.py                   전체 흐름 오케스트레이션 (다이렉트 WS 모드)
  web/index.html           프론트엔드 (SSE 수신·렌더)
tokens.json.example        멀티 계정용 채널별 토큰 설정 예시
megaphone_config.json      런타임 설정 (토큰, 채널 정보 캐시) — gitignore 대상
```

## 개발 / 테스트

게임 클라이언트가 필요 없는 순수 로직(프로토콜·토큰 해석·채널 선택 등)은 단위
테스트로 검증됩니다.

```
pip install -r requirements-dev.txt
ruff check .     # 린트
pytest           # 단위 테스트
```

### CI / CD

- **CI** — `main` 푸시 및 PR마다 Python 3.11·3.12에서 ruff 린트와
  pytest를 실행합니다 (`.github/workflows/test.yml`).
- **CD** — `v*` 태그를 푸시하면 소스를 zip으로 묶어 GitHub Release를 생성합니다
  (`.github/workflows/release.yml`).

  ```
  git tag v1.0.0
  git push origin v1.0.0
  ```

## 1세션 제한

마피아42 서버는 **토큰(=계정) 1개당 활성 채널 세션을 1개만** 허용합니다. 한 계정으로
여러 채널에 동시에 붙으면 서로 강퇴됩니다. 이것이 CDP 패시브 모니터를 사용하는
이유입니다 — 게임의 라이브 세션을 관찰만 하기 때문에 세션 충돌이 발생하지 않습니다.

## 참고

- CDP 패시브 모니터는 게임이 `--remote-debugging-port=9222`로 실행되어 있어야 합니다.
  바탕화면의 `mafia42_debug.bat`을 사용하면 편리합니다.
- 다이렉트 WS 모드의 인증 토큰(Google OAuth, `ya29...`)은 약 1시간 후 만료됩니다.
  장시간 실행 중 재접속이 계속 거부되면 게임에서 채널을 다시 이동해 토큰을 갱신하세요.
- 채널 캡처가 안 되면 토큰을 수동 입력할 수 있지만, 채널별 실제 서버 IP는
  CDP 캡처로만 얻을 수 있으므로 채널 이동 캡처가 필요합니다.
