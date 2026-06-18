"""채널별 접속 인원 폴링.

마피아42의 채널 목록 API(channel_ko.php) 응답에는 채널마다 user_count(접속 인원)가
들어 있다. CDP로 캡처해 둔 그 API URL을 주기적으로 다시 호출해 인원수를 갱신한다.
(외부 사이트들이 채널 인원을 실시간 표시하는 것도 이 엔드포인트를 폴링하는 방식이다.)

엔드포인트가 인증을 요구할 수 있으므로, 게임 세션에서 캡처한 요청 헤더를 그대로
실어 보낸다(없으면 일반 User-Agent로 폴백).
"""
import json
import threading
import time
import urllib.request

from .config import EXCLUDED_CHANNELS
from .store import store

POLL_INTERVAL = 15  # 초. 채널 인원 갱신 주기.

_DEFAULT_HEADERS = {'User-Agent': 'Mozilla/5.0'}


def fetch_channel_list(url, headers=None):
    """channel_ko.php URL을 호출해 채널 목록(JSON 배열)을 반환. 실패 시 []."""
    req = urllib.request.Request(url, headers=headers or _DEFAULT_HEADERS)
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = json.loads(resp.read().decode('utf-8', errors='replace'))
    return data if isinstance(data, list) else []


def populations_from_list(channels):
    """채널 목록에서 {channel_id(int): user_count(int)} 매핑을 추출.

    감시 제외 채널(EXCLUDED_CHANNELS, 예: 비밀)은 인원 표시도 하지 않으므로 건너뛴다.
    초기 시드와 라이브 폴링 양쪽이 이 함수를 거치므로 여기서 한 번에 차단한다.
    """
    out = {}
    for ch in channels:
        try:
            cid = int(ch['channel_id'])
        except (KeyError, TypeError, ValueError):
            continue
        if cid in EXCLUDED_CHANNELS:
            continue
        try:
            out[cid] = int(ch.get('user_count', 0))
        except (TypeError, ValueError):
            continue
    return out


class PopulationPoller(threading.Thread):
    """채널 인원을 주기적으로 갱신해 store 에 반영하는 백그라운드 스레드."""

    def __init__(self, url, headers=None, interval=POLL_INTERVAL):
        super().__init__(daemon=True)
        self.url = url
        self.headers = headers
        self.interval = interval
        self.running = True

    def run(self):
        while self.running:
            try:
                pops = populations_from_list(fetch_channel_list(self.url, self.headers))
                if pops:
                    store.set_populations(pops)
            except Exception as e:
                print(f"  [인원] 갱신 실패: {e}")
            time.sleep(self.interval)
