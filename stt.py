"""
stt_module.py
B 파트 — 음성 축(STT) 핵심 모듈

전사 + 발화 지표(발화시간·어절수·침묵구간) + 마무리 지표(종결여부·말끝흐림·무음종료)를
계산하고, session_id + question_id 기준으로 캐시한다.

설계 원칙: 순수 함수. 입력은 오디오 경로 + 식별자, 출력은 정해진 JSON(dict).
HTTP/DB 접근 코드는 이 파일 안에 넣지 않는다 — 서버 쪽에서는 얇은 어댑터만 붙여서
process_answer() 하나만 호출하면 된다.

필요 패키지: faster-whisper
    pip install faster-whisper
"""

import os
import json
from faster_whisper import WhisperModel

# ---------------------------------------------------------------------------
# 설정값 (Q1/Q2 대조 실험으로 확정한 값들)
# ---------------------------------------------------------------------------

MODEL_NAME = "large-v3"
DEVICE = "cuda"
COMPUTE_TYPE = "float16"

SILENCE_THRESHOLD = 1.0  # 초. 이보다 길게 비면 "의미 있는 침묵"으로 판단.
ENDING_PATTERNS = ["습니다", "니다", "겠습니다", "합니다", "됩니다", "죠", "네요", "어요", "아요"]

# 나중에 C의 Redis 인프라가 준비되면 이 경로 대신 Redis 클라이언트로 교체.
# 배포 환경마다 다를 수 있어서 환경변수로 오버라이드 가능하게 해둠.
CACHE_DIR = os.environ.get("STT_CACHE_DIR", "./stt_cache")
os.makedirs(CACHE_DIR, exist_ok=True)


# ---------------------------------------------------------------------------
# 모델 (모듈 최초 사용 시 한 번만 로드)
# ---------------------------------------------------------------------------

_model = None


def _get_model() -> WhisperModel:
    global _model
    if _model is None:
        _model = WhisperModel(MODEL_NAME, device=DEVICE, compute_type=COMPUTE_TYPE)
    return _model


# ---------------------------------------------------------------------------
# 1. 전사
# ---------------------------------------------------------------------------

def transcribe_core(audio_path: str):
    """오디오 경로를 받아 whisper segments 리스트와 전체 길이를 반환한다."""
    model = _get_model()
    segments, info = model.transcribe(
        audio_path, language="ko", word_timestamps=True, beam_size=5
    )
    return list(segments), info.duration


# ---------------------------------------------------------------------------
# 2. 발화 지표 — 발화시간, 어절수, 침묵구간
# (발화시간·어절수는 A의 길이 게이트에도 쓰이므로 우선순위 높음)
# ---------------------------------------------------------------------------

def compute_speech_metrics(segments, silence_threshold: float = SILENCE_THRESHOLD) -> dict:
    full_text = " ".join(seg.text for seg in segments).strip()
    word_times = [(float(w.start), float(w.end)) for seg in segments for w in seg.words]

    speech_duration = (word_times[-1][1] - word_times[0][0]) if word_times else 0.0
    word_count = len(full_text.split())

    silences = [
        {
            "start": round(word_times[i - 1][1], 2),
            "end": round(word_times[i][0], 2),
            "duration": round(word_times[i][0] - word_times[i - 1][1], 2),
        }
        for i in range(1, len(word_times))
        if word_times[i][0] - word_times[i - 1][1] > silence_threshold
    ]

    return {
        "text": full_text,
        "speech_duration_sec": round(speech_duration, 2),
        "word_count": word_count,
        "silence_segments": silences,
        "silence_count": len(silences),
        "silence_total_sec": round(sum(s["duration"] for s in silences), 2),
    }


# ---------------------------------------------------------------------------
# 3. 마무리 지표 — 종결 여부, 말끝 흐림, 무음 종료
#
# 알려진 한계: is_concluded는 종결어미(습니다/네요 등) 존재 여부만 본다.
# 내용이 부실해도 습관적으로 "감사합니다"로 끝내면 True가 나올 수 있다.
# ---------------------------------------------------------------------------

def compute_ending_metrics(full_text: str, segments, audio_duration: float) -> dict:
    text_stripped = full_text.strip()
    is_concluded = bool(any(text_stripped.endswith(p) for p in ENDING_PATTERNS))

    all_words = [w for seg in segments for w in seg.words]
    last_word_end = float(all_words[-1].end) if all_words else 0.0
    trailing_silence = float(audio_duration) - last_word_end

    # 제대로 끝맺었으면(is_concluded=True) 뒤에 여백이 있어도 문제 삼지 않는다.
    silent_ending = bool((not is_concluded) and (trailing_silence > SILENCE_THRESHOLD))
    trailing_off = bool(not is_concluded)

    return {
        "is_concluded": is_concluded,
        "trailing_off": trailing_off,
        "silent_ending": silent_ending,
        "trailing_silence_sec": round(trailing_silence, 2),
    }


# ---------------------------------------------------------------------------
# 4. 캐시 — session_id + question_id
# (세션 중 꼬리질문 생성용으로 이미 전사한 걸 리포트가 재사용)
# ---------------------------------------------------------------------------

def _cache_path(session_id: str, question_id: str) -> str:
    return os.path.join(CACHE_DIR, f"{session_id}__{question_id}.json")


def cache_get(session_id: str, question_id: str):
    path = _cache_path(session_id, question_id)
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError:
        # 이전 쓰기가 중간에 실패해 깨진 캐시 파일이 남은 경우 — 지우고 재계산
        os.remove(path)
        return None


def cache_set(session_id: str, question_id: str, data: dict):
    def _default(o):
        # numpy/pandas 스칼라 등 JSON이 모르는 타입을 파이썬 기본 타입으로 변환
        try:
            return o.item()
        except AttributeError:
            return str(o)

    final_path = _cache_path(session_id, question_id)
    tmp_path = final_path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, default=_default)
    os.replace(tmp_path, final_path)  # 다 써진 뒤에만 실제 파일명으로 교체 (원자적 쓰기)


# ---------------------------------------------------------------------------
# 5. 진입점 — 서버는 이 함수 하나만 호출하면 된다
# ---------------------------------------------------------------------------

def process_answer(
    audio_path: str,
    session_id: str,
    question_id: str,
    is_timeout: bool = False,
) -> dict:
    """
    입력: 오디오 경로, session_id, question_id, is_timeout(리포트 표시용, 계산엔 안 씀)
    출력: 전사 텍스트 + 발화 지표 + 마무리 지표를 담은 dict (JSON 직렬화 가능)
    캐시가 있으면 그대로 반환하고, 없으면 계산 후 캐시에 저장한다.
    """
    cached = cache_get(session_id, question_id)
    if cached is not None:
        return cached

    segments, duration = transcribe_core(audio_path)
    speech_metrics = compute_speech_metrics(segments)
    ending_metrics = compute_ending_metrics(speech_metrics["text"], segments, duration)

    result = {
        "session_id": session_id,
        "question_id": question_id,
        "is_timeout": bool(is_timeout),
        **speech_metrics,
        **ending_metrics,
    }

    cache_set(session_id, question_id, result)
    return result
