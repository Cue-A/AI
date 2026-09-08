"""답변 오디오를 텍스트와 발화 지표로 바꾼다.

    audio_url  →  내려받기  →  ai/stt.py  →  발화시간 · 어절수 · 전사 텍스트

더미 모드에서는 이 모듈을 부르지 않는다. `dummy.answer_length()`가 파일명으로
길이를 지어내며, 백엔드가 지금 검증하고 있는 동작이 그것이다.

세 곳이 이 결과를 쓴다.

    길이 게이트    발화시간 · 어절수 → session_plan이 되묻기 여부를 정한다
    꼬리질문       전사 텍스트 → 직전 답변을 파고드는 질문을 만든다
    리포트         B가 캐시해 둔 전사 결과를 재사용한다

faster-whisper는 GPU가 있어야 돌아간다. import를 함수 안으로 미뤄서
STT를 쓰지 않는 배포에서는 이 모듈을 불러도 아무 일이 없게 했다.
"""
import logging
import os
import tempfile
from typing import NamedTuple

import httpx2

logger = logging.getLogger("cue.ai.answers")

# STT만 따로 켜는 스위치.
#
# 전사 확인과 LLM 질문 생성은 성격이 다르다. Whisper는 우리 GPU에서 돌아
# 요금이 없고, LLM은 호출마다 돈이 나간다. 둘이 한 스위치에 묶여 있으면
# "전사가 되는지"만 보려 해도 주질문 생성까지 돌아 78원이 나간다.
#
#   AI_MODE=dummy                    전사 안 함. 백엔드가 쓰는 기본값
#   AI_MODE=dummy + USE_STT=1        전사만 함. 질문은 고정 문장. 요금 0원
#   AI_MODE=llm                      전사도 하고 질문도 생성. 요금 발생
STT_ENV = "USE_STT"

DOWNLOAD_TIMEOUT_SEC = 60.0

# 60초 답변이 webm으로 1MB 안팎이다. 넉넉히 잡되 무한정 받지는 않는다.
MAX_BYTES = 50 * 1024 * 1024


def stt_enabled() -> bool:
    """전사를 실제로 돌리는가.

    USE_STT를 켜면 AI_MODE가 dummy여도 전사한다. GPU 담당이 요금 없이
    통합을 확인할 수 있게 하려는 것이다.
    """
    from ai import llm

    if os.environ.get(STT_ENV, "").strip().lower() in ("1", "true", "yes", "on"):
        return True
    return llm.llm_enabled()


class SttError(Exception):
    """전사에 실패했다. 계약서의 STT_FAILED로 옮겨진다."""


class AnswerText(NamedTuple):
    """답변 하나에서 뽑아낸 값.

    duration_sec와 word_count는 session_plan의 길이 게이트가 쓰고,
    text는 꼬리질문 생성이 쓴다.
    """

    duration_sec: int
    word_count: int
    text: str


def _download(audio_url: str) -> bytes:
    if not audio_url:
        raise SttError("audio_url이 비어 있습니다")
    try:
        with httpx2.Client(timeout=DOWNLOAD_TIMEOUT_SEC, follow_redirects=True) as client:
            response = client.get(audio_url)
            response.raise_for_status()
            raw = response.content
    except httpx2.HTTPStatusError as e:
        # presigned URL 만료가 가장 흔하다
        raise SttError(
            f"답변 음성을 내려받지 못했습니다 (HTTP {e.response.status_code}). "
            "presigned URL이 만료되었을 수 있습니다"
        ) from e
    except httpx2.HTTPError as e:
        raise SttError(f"답변 음성을 내려받지 못했습니다: {type(e).__name__}") from e

    if not raw:
        raise SttError("답변 음성이 비어 있습니다")
    if len(raw) > MAX_BYTES:
        raise SttError(f"답변 음성이 너무 큽니다. {MAX_BYTES // 1024 // 1024}MB 이하만 처리합니다")
    return raw


def transcribe(
    *,
    audio_url: str,
    session_id: str,
    question_id: str,
    is_timeout: bool = False,
) -> AnswerText:
    """답변 오디오를 전사하고 발화 지표를 뽑는다.

    stt.process_answer가 session_id + question_id로 캐시하므로,
    리포트가 같은 답변을 다시 전사하지 않는다.
    """
    raw = _download(audio_url)

    # faster-whisper는 파일 경로를 받는다. 디스크에 잠깐 두었다 지운다.
    # 답변 음성은 민감정보라 처리 후 남기지 않는다.
    suffix = os.path.splitext(audio_url.split("?")[0])[1] or ".webm"
    path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as f:
            f.write(raw)
            path = f.name

        from ai import stt

        result = stt.process_answer(
            path, session_id=session_id, question_id=question_id, is_timeout=is_timeout
        )
    except SttError:
        raise
    except Exception as e:
        raise SttError(f"음성 인식에 실패했습니다: {type(e).__name__}") from e
    finally:
        if path and os.path.exists(path):
            try:
                os.remove(path)
            except OSError:
                logger.warning("임시 음성 파일을 지우지 못했습니다: %s", path)

    return _to_answer_text(result)


def _to_answer_text(result: dict) -> AnswerText:
    """stt.process_answer의 dict를 길이 게이트가 쓰는 값으로 옮긴다.

    키 이름이 바뀌면 여기만 고치면 된다. 게이트와 꼬리질문은 AnswerText만 본다.
    """
    try:
        duration = float(result["speech_duration_sec"])
        word_count = int(result["word_count"])
    except (KeyError, TypeError, ValueError) as e:
        raise SttError(f"음성 인식 결과를 해석하지 못했습니다: {e}") from e

    return AnswerText(
        duration_sec=int(round(duration)),
        word_count=word_count,
        text=(result.get("text") or "").strip(),
    )
