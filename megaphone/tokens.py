"""채널별 인증 토큰 해석.

마피아42 서버는 토큰(=계정) 1개당 활성 채널 세션을 1개만 허용한다. 따라서
여러 채널의 '채널 확성기'를 동시에 받으려면 채널마다 서로 다른 계정 토큰이
필요하다. 이 모듈은 "채널 → 토큰" 매핑을 만들어 멀티 계정 확장을 가능하게 한다.

현재(단일 계정) 모드:
  CDP로 캡처한 토큰 1개를 선택한 채널에 그대로 사용한다.

멀티 계정 모드:
  프로젝트 루트의 tokens.json 에 채널별 토큰을 넣으면 그 채널은 해당 토큰을 쓴다.

    // tokens.json
    {
      "0": "TEST_TOKEN_A",   // 초보 채널 전용 계정 토큰
      "1": "TEST_TOKEN_B"    // 1채널 전용 계정 토큰
    }

  여기 등록되지 않은 채널은 CDP로 캡처한 토큰으로 폴백한다(있을 경우).
"""
import json
from pathlib import Path

# 프로젝트 루트(megaphone/ 의 상위)에 둔다.
TOKENS_FILE = Path(__file__).resolve().parent.parent / "tokens.json"


def load_token_overrides(path=TOKENS_FILE):
    """tokens.json 에서 {channel_id(int): token} 매핑을 로드. 없거나 깨졌으면 {}."""
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"[!] tokens.json 파싱 실패 - 무시하고 진행: {e}")
        return {}
    overrides = {}
    for k, v in raw.items():
        if not v:
            continue
        try:
            overrides[int(k)] = v
        except (TypeError, ValueError):
            print(f"[!] tokens.json 의 채널 키 '{k}' 가 정수가 아님 - 건너뜀")
    return overrides


def resolve_channel_tokens(channel_ids, captured_token, overrides=None):
    """선택한 채널 각각에 사용할 토큰을 결정해 {channel_id: token} 으로 반환.

    채널별 override(tokens.json)가 있으면 우선, 없으면 captured_token 으로 폴백한다.
    토큰을 전혀 구하지 못한 채널은 매핑에서 제외된다(호출 측에서 건너뜀/경고 처리).
    """
    overrides = overrides or {}
    result = {}
    for cid in channel_ids:
        token = overrides.get(cid) or captured_token
        if token:
            result[cid] = token
    return result
