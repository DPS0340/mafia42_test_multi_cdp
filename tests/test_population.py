"""population.py 인원 파싱 단위 테스트 (네트워크 불필요)."""
from megaphone.config import EXCLUDED_CHANNELS
from megaphone.population import populations_from_list


def test_populations_basic():
    channels = [
        {"channel_id": "0", "user_count": "214"},
        {"channel_id": 1, "user_count": 938},
    ]
    assert populations_from_list(channels) == {0: 214, 1: 938}


def test_populations_skips_malformed_entries():
    channels = [
        {"channel_id": "0", "user_count": "100"},
        {"channel_id": "x", "user_count": "5"},   # 채널 id 정수 아님 → 건너뜀
        {"user_count": "7"},                       # channel_id 없음 → 건너뜀
    ]
    assert populations_from_list(channels) == {0: 100}


def test_populations_missing_user_count_defaults_zero():
    assert populations_from_list([{"channel_id": "2"}]) == {2: 0}


def test_populations_empty():
    assert populations_from_list([]) == {}


def test_populations_excludes_secret_channel():
    # 19(비밀)은 감시 제외 채널이라 인원 매핑에서 빠진다 (초기 시드/라이브 폴링 양쪽).
    channels = [
        {"channel_id": 1, "user_count": 100},
        {"channel_id": "19", "user_count": 5},
    ]
    pops = populations_from_list(channels)
    assert 19 not in pops
    assert pops == {1: 100}


def test_excluded_channels_contains_secret():
    assert 19 in EXCLUDED_CHANNELS
