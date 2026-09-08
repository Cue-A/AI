"""질문 텍스트를 음성으로 바꾼다.

    질문 텍스트 + 페르소나  →  ai/tts.py  →  mp3 URL

**음성 합성이 실패해도 세션을 멈추지 않는다.** 계약서 8장이 정한 규칙이다.

    TTS_FAILED   재시도 없음. audio_url을 null로 두고 텍스트만 진행한다

질문 텍스트는 이미 만들어진 뒤라, 음성이 없다고 면접을 끊는 것은 손해가 크다.
프론트는 audio_url이 null이면 텍스트만 띄운다.

STT와 마찬가지로 LLM과 스위치를 분리했다. TTS는 외부 API를 호출해 요금이
붙으므로, 켜고 끄는 것을 따로 정할 수 있어야 한다.

    AI_MODE=dummy                 합성 안 함. 샘플 mp3 하나가 고정으로 나간다
    AI_MODE=llm                   합성 안 함. 질문만 생성한다
    USE_TTS=1                     실제로 합성한다. 요금이 붙는다

B가 붙일 모듈의 모양은 이렇다.

    # ai/tts.py
    def synthesize(text: str, persona: str) -> str:
        \"\"\"음성을 만들어 어딘가에 올리고 재생 가능한 URL을 준다.\"\"\"

`persona`는 "friendly" 또는 "pressure"다. 둘이 속도와 톤에서 구분되어야 한다.
"""
import logging
import os
from typing import Optional

logger = logging.getLogger("cue.ai.voice")

TTS_ENV = "USE_TTS"


def tts_enabled() -> bool:
    """실제로 음성을 합성하는가.

    AI_MODE와 묶지 않는다. TTS는 호출마다 요금이 붙어서, 질문 생성만
    확인하고 싶을 때 음성까지 만들면 그만큼 돈이 나간다.
    """
    return os.environ.get(TTS_ENV, "").strip().lower() in ("1", "true", "yes", "on")


def synthesize(text: str, persona: str) -> Optional[str]:
    """질문 음성의 URL. 만들지 못하면 None.

    None이면 부르는 쪽이 audio_url을 null로 둔다. 예외를 올리지 않는 것은
    계약서 8장 때문이다 — TTS 실패는 재시도하지 않고 텍스트로 진행한다.
    """
    if not tts_enabled():
        return None
    if not text.strip():
        return None

    try:
        from ai import tts
    except ImportError:
        logger.warning("USE_TTS가 켜져 있지만 ai/tts.py가 없습니다. 텍스트로 진행합니다")
        return None

    fn = getattr(tts, "synthesize", None)
    if fn is None:
        logger.warning(
            "ai/tts.py에 synthesize(text, persona)가 없습니다. 텍스트로 진행합니다"
        )
        return None

    try:
        url = fn(text, persona)
    except Exception as e:
        # 여기서 예외를 올리면 질문이 이미 만들어졌는데도 세션이 끊긴다
        logger.warning("음성 합성에 실패했습니다 (%s). 텍스트로 진행합니다", type(e).__name__)
        return None

    if not url or not isinstance(url, str):
        logger.warning("음성 합성 결과가 URL이 아닙니다. 텍스트로 진행합니다")
        return None

    return url
