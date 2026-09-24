"""ai/tts.py
B 파트 — 음성 축(TTS) 핵심 모듈

ai/voice.py가 부르는 진입점은 synthesize(text, persona, session_id, question_id) 하나뿐이다.
성공하면 S3에 올라간 오디오 URL(str)을, 실패하면 None을 반환한다.

세션 번호와 질문 번호를 받으면 백엔드 경로 규칙
sessions/{sessionId}/questions/{questionId}.mp3 에 올린다. 없으면 예전처럼
임의 경로에 올리는데, 실제 S3에서는 권한 밖이라 실패한다.

voice.py가 이미 ImportError / 속성 없음 / 예외 / URL 아닌 반환값까지 전부
방어하고 있지만(tests/test_voice.py 참고), 이 파일 단독으로도 안전하도록
모든 실패를 여기서 흡수한다 — 절대 예외를 밖으로 던지지 않는다.
계약서 8장: TTS_FAILED는 재시도 없이 audio_url을 null로 두고 텍스트로 진행한다.

S3 업로드는 새로 만들지 않고 infra/gaze_analysis/s3_upload.py가 이미 준비해 둔
upload_audio_to_s3()를 그대로 쓴다 (그 파일 docstring: "질문 음성(TTS 결과)을
S3에 업로드. AI가 직접 올리는 구조" — TTS용으로 미리 만들어 둔 것).
S3_BUCKET이 아직 없으면(백엔드 P0 항목) RuntimeError가 나는데, 여기서 잡아서
None을 반환하므로 버킷이 오기 전까지는 항상 "합성 실패 → 텍스트만 진행"으로
동작한다. 버킷 정보가 오면 환경변수만 채우면 되고 이 파일은 안 건드려도 된다.

필요 패키지: httpx2, boto3 (둘 다 requirements-base.txt에 이미 있음)

환경 변수:
    TYPECAST_API_KEY   (필수) Typecast API 키. 코드에 하드코딩 금지 —
                        GitHub에 커밋되는 순간 키가 그대로 노출된다.
    TYPECAST_VOICE_ID  (선택) 기본값은 지금까지 검증에 쓴 "다은" 목소리.
    S3_BUCKET / S3_REGION / S3_AUDIO_PREFIX
                        infra/gaze_analysis/s3_upload.py가 이미 정의한 것을
                        그대로 쓴다. 새로 만들지 않는다.
    TTS_CACHE_DIR      (선택) 같은 (text, persona) 조합을 다시 합성·업로드하지
                        않기 위한 로컬 캐시 경로. 기본 "./tts_cache".
                        AI_MODE=dummy처럼 고정 문장이 반복될 때 Typecast 요금과
                        S3 중복 업로드를 아낀다.
"""
import hashlib
import json
import logging
import os
import tempfile
from typing import Optional

import httpx2

from infra.gaze_analysis.s3_upload import question_audio_key, upload_audio_to_s3

logger = logging.getLogger("cue.ai.tts")

# ---------------------------------------------------------------------------
# 설정값
# ---------------------------------------------------------------------------

TYPECAST_API_KEY = os.environ.get("TYPECAST_API_KEY", "")
VOICE_ID = os.environ.get("TYPECAST_VOICE_ID", "tc_69f2e455ea79fd197aa0476f")  # "다은"
MODEL_NAME = "ssfm-v30"
API_URL = "https://api.typecast.ai/v1/text-to-speech"
REQUEST_TIMEOUT_SEC = 60.0

CACHE_DIR = os.environ.get("TTS_CACHE_DIR", "./tts_cache")

# 페르소나별 파라미터. "friendly"/"pressure" 둘로 고정 (계약서 — Persona 리터럴과 동일).
#
# 알려진 한계: pressure(emotion_preset=normal, intensity=0.6, tempo=1.1)는 실제
# 청취로 검증까지 마쳤다. friendly는 검증 당시 previous_text/next_text를 넘기는
# "smart" 방식으로만 들어봤고, 여기 기본값인 preset 방식은 아직 청취 검증 전이다.
# ai/voice.py의 계약이 synthesize(text, persona) 두 인자로 고정돼 있어 문맥을
# 넘길 방법이 없으므로, 우선 preset으로 맞춰두고 팀 청취 후 조정한다.
PERSONA_CONFIG = {
    "friendly": {
        "emotion_preset": "normal",
        "emotion_intensity": 0.3,
        "audio_tempo": 1.0,
    },
    "pressure": {
        "emotion_preset": "normal",
        "emotion_intensity": 0.6,
        "audio_tempo": 1.1,
    },
}


# ---------------------------------------------------------------------------
# 캐시 — 같은 (text, persona)는 다시 합성/업로드하지 않는다
# ---------------------------------------------------------------------------

def _cache_key(text: str, persona: str) -> str:
    raw = f"{persona}::{text}".encode("utf-8")
    return hashlib.sha1(raw).hexdigest()[:16]


def _cache_path(key: str) -> str:
    os.makedirs(CACHE_DIR, exist_ok=True)
    return os.path.join(CACHE_DIR, f"{key}.json")


def _cache_get(key: str) -> Optional[str]:
    path = _cache_path(key)
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f).get("url")
    except (json.JSONDecodeError, OSError):
        return None


def _cache_set(key: str, url: str) -> None:
    path = _cache_path(key)
    tmp_path = path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump({"url": url}, f)
    os.replace(tmp_path, path)  # 원자적 쓰기


# 합성한 음성 자체도 (text, persona) 기준으로 남긴다.
#
# 올리는 경로가 세션마다 달라서 URL 캐시는 세션을 넘어 재사용할 수 없다.
# 재연습처럼 같은 문장을 다시 읽을 때 Typecast를 또 부르면 요금이 그만큼 나가므로,
# 음성은 재사용하고 업로드만 새 경로로 한다.
def _audio_path(key: str) -> str:
    os.makedirs(CACHE_DIR, exist_ok=True)
    return os.path.join(CACHE_DIR, f"{key}.mp3")


def _audio_get(key: str) -> Optional[bytes]:
    path = _audio_path(key)
    if not os.path.exists(path):
        return None
    try:
        with open(path, "rb") as f:
            data = f.read()
    except OSError:
        return None
    return data or None


def _audio_set(key: str, data: bytes) -> None:
    path = _audio_path(key)
    tmp_path = path + ".tmp"
    with open(tmp_path, "wb") as f:
        f.write(data)
    os.replace(tmp_path, path)


# ---------------------------------------------------------------------------
# Typecast 호출
# ---------------------------------------------------------------------------

def _call_typecast(text: str, persona: str) -> bytes:
    config = PERSONA_CONFIG[persona]
    payload = {
        "voice_id": VOICE_ID,
        "text": text,
        "model": MODEL_NAME,
        "language": "kor",
        "prompt": {
            "emotion_type": "preset",
            "emotion_preset": config["emotion_preset"],
            "emotion_intensity": config["emotion_intensity"],
        },
        "output": {
            "audio_tempo": config["audio_tempo"],
            "audio_format": "mp3",
        },
    }
    headers = {"X-API-KEY": TYPECAST_API_KEY, "Content-Type": "application/json"}

    with httpx2.Client(timeout=REQUEST_TIMEOUT_SEC) as client:
        res = client.post(API_URL, headers=headers, json=payload)

    content_type = res.headers.get("Content-Type", "")
    if res.status_code != 200 or "audio" not in content_type:
        # 400(잘못된 voice_id) / 401(인증 실패) / 402(크레딧 부족) / 404 / 422 / 429 등
        raise RuntimeError(
            f"Typecast 합성 실패 status={res.status_code} body={res.text[:300]}"
        )
    if not res.content:
        raise RuntimeError("Typecast 응답 본문이 비어 있습니다")
    return res.content


# ---------------------------------------------------------------------------
# 진입점 — ai/voice.py가 이 함수 하나만 호출한다
# ---------------------------------------------------------------------------

def synthesize(
    text: str,
    persona: str,
    session_id: Optional[str] = None,
    question_id: Optional[str] = None,
) -> Optional[str]:
    """질문 음성을 합성해 S3 URL을 반환한다. 실패하면 None.

    text: 합성할 문장
    persona: "friendly" 또는 "pressure" (그 외 값은 friendly로 대체하고 경고만 남김)
    session_id · question_id: 있으면 sessions/{sessionId}/questions/{questionId}.mp3에 올린다
    """
    if not text or not text.strip():
        return None

    if persona not in PERSONA_CONFIG:
        logger.warning("[TTS] 알 수 없는 persona=%r — friendly로 대체합니다", persona)
        persona = "friendly"

    if not TYPECAST_API_KEY:
        logger.error("[TTS] TYPECAST_API_KEY가 설정되지 않았습니다")
        return None

    audio_key = _cache_key(text, persona)
    s3_key = question_audio_key(session_id, question_id) if session_id and question_id else None
    # 올린 주소는 경로마다 다르다. 세션 · 질문이 있으면 그것까지 키에 넣는다
    key = _cache_key(f"{s3_key}::{text}", persona) if s3_key else audio_key
    cached = _cache_get(key)
    if cached:
        return cached

    audio_bytes = _audio_get(audio_key)
    if audio_bytes is None:
        try:
            audio_bytes = _call_typecast(text, persona)
        except httpx2.HTTPError as e:
            logger.error("[TTS] Typecast 요청 실패: %s", e)
            return None
        except RuntimeError as e:
            logger.error("[TTS] %s", e)
            return None
        try:
            _audio_set(audio_key, audio_bytes)
        except OSError:
            logger.warning("[TTS] 합성한 음성을 캐시에 남기지 못했습니다")

    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
            f.write(audio_bytes)
            tmp_path = f.name

        url = upload_audio_to_s3(tmp_path, key=s3_key)
    except Exception as e:
        # S3_BUCKET 미설정(RuntimeError)도 여기서 걸린다 — 백엔드가 버킷을
        # 주기 전까지는 항상 이 경로로 빠져서 None을 반환하게 된다.
        logger.error("[TTS] S3 업로드 실패: %s", e)
        return None
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                logger.warning("[TTS] 임시 mp3 파일을 지우지 못했습니다: %s", tmp_path)

    _cache_set(key, url)
    return url
