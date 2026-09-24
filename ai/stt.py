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
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # 타입 힌트용. 실행 시에는 불러오지 않는다
    from faster_whisper import WhisperModel

# ---------------------------------------------------------------------------
# 설정값 (Q1/Q2 대조 실험으로 확정한 값들)
# ---------------------------------------------------------------------------

MODEL_NAME = os.environ.get("STT_MODEL", "large-v3")
# GPU가 없는 곳에서도 불러올 수 있어야 한다. 개발자 노트북에서는 STT_DEVICE=cpu.
DEVICE = os.environ.get("STT_DEVICE", "cuda")
COMPUTE_TYPE = os.environ.get("STT_COMPUTE_TYPE", "float16")

SILENCE_THRESHOLD = 1.0  # 초. 이보다 길게 비면 "의미 있는 침묵"으로 판단.
ENDING_PATTERNS = ["습니다", "니다", "겠습니다", "합니다", "됩니다", "죠", "네요", "어요", "아요"]

# 말하기 축 점수(speech_score) 변환에서 마무리 지표에 매기는 감점.
# 잠정치 — 3명 데이터로 임시로 잡았다. 실제 답변이 쌓이면 앵커 답변 세트로 재조정 필요.
SPEECH_TRAILING_OFF_PENALTY = 8   # 종결어미로 끝맺지 못함
SPEECH_SILENT_ENDING_PENALTY = 7  # 거기에 더해 끝에 긴 침묵까지 있음 (trailing_off일 때만 발생)

# 나중에 C의 Redis 인프라가 준비되면 이 경로 대신 Redis 클라이언트로 교체.
# 배포 환경마다 다를 수 있어서 환경변수로 오버라이드 가능하게 해둠.
CACHE_DIR = os.environ.get("STT_CACHE_DIR", "./stt_cache")


# ---------------------------------------------------------------------------
# 모델 (모듈 최초 사용 시 한 번만 로드)
# ---------------------------------------------------------------------------

_model = None


def _get_model() -> "WhisperModel":
    """모델은 처음 쓸 때 한 번만 올린다.

    faster-whisper를 여기서 불러온다. 모듈 맨 위에서 불러오면 STT를 쓰지 않는
    더미 배포에서도 패키지를 요구하게 되어 이미지가 무거워지고 import가 깨진다.
    """
    global _model
    if _model is None:
        from faster_whisper import WhisperModel

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
#
# 수정 이력: Whisper가 문장 끝에 마침표(.)를 붙이느냐 마느냐가 모델·발화마다
# 들쭉날쭉해서, endswith 비교 전에 트레일링 문장부호를 제거하도록 고쳤다.
# 안 그러면 내용은 똑같은데 마침표 유무만으로 is_concluded가 뒤집히고, 그게
# speech_score의 마무리 감점(-15점)까지 좌우해버린다 — tiny/medium/large-v3
# 세 모델 비교 중 large-v3에서 실제로 이 문제가 점수를 92점까지 튀게 만든 걸 확인함.
# ---------------------------------------------------------------------------

def compute_ending_metrics(full_text: str, segments, audio_duration: float) -> dict:
    text_stripped = full_text.strip()
    # Whisper가 붙이거나 안 붙이거나 하는 문장부호는 종결 판정에서 무시한다.
    text_for_ending_check = text_stripped.rstrip(".!?~…‥ ")
    is_concluded = bool(any(text_for_ending_check.endswith(p) for p in ENDING_PATTERNS))

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
# 3-2. 머뭇거림 지표 — 필러워드 개수 대신 쓰는 유창성 지표
#
# 설계 배경: Whisper가 "음"/"어" 같은 필러를 텍스트로 안 뱉고 지워버리는 걸
# 확인했고(직접 청취로 대조 검증), 그 필러가 차지했던 시간은 어차피 단어 사이
# gap(=silence_segments)에 이미 반영되고 있다는 것도 확인했다 — 필러로 채운
# 시간이든 진짜 침묵이든 Whisper 입장에선 둘 다 "인식된 단어 사이의 빈 시간"
# 이라 구분이 안 된다. 그래서 필러를 텍스트로 복원하는 시도(파인튜닝 등) 대신,
# 이미 있는 침묵 지표에 발화 속도 변동성 + 인접 단어 반복을 더해서
# "얼마나 매끄럽게 말했는가"를 종합적으로 표현하기로 했다.
#
# 리포트 계약(축별 세부 지표 axes.speech.metrics)에는 이 중 hesitation_score를
# 대표 필드로 올린다. speech_rate_cv/repetition_count는 원인 파악용 보조 필드.
#
# 알려진 한계:
# - repetition_count는 "바로 인접한 동일 토큰"만 잡는 보수적인 버전이다.
#   ("그니까 그니까 저는" 처럼 사이에 다른 말이 끼면 못 잡음)
# - hesitation_score의 가중치(50/30/20)는 3명 데이터로 임시로 잡은 값이라
#   실제 서비스 데이터가 쌓이면 재조정이 필요하다.
# ---------------------------------------------------------------------------

def _extract_words(segments):
    """(단어 텍스트, 시작, 끝) 튜플 리스트. compute_speech_metrics의 word_times와
    달리 반복 탐지를 위해 텍스트도 같이 들고 있어야 해서 별도로 뽑는다."""
    words = []
    for seg in segments:
        for w in seg.words:
            text = w.word.strip()
            if text:
                words.append((text, float(w.start), float(w.end)))
    return words


def compute_fluency_metrics(segments, silence_segments: list, speech_duration_sec: float) -> dict:
    words = _extract_words(segments)

    # --- 발화 속도 변동성: 침묵으로 끊기는 "이어 말한 덩어리" 단위로 속도(단어/초)를
    # 구하고, 덩어리 간 속도의 변동계수(CV = 표준편차/평균)를 낸다.
    # 들쭉날쭉할수록(급하게 몰아치다 갑자기 느려지는 식) 값이 커진다.
    boundaries = sorted(s["start"] for s in silence_segments)
    chunk_rates = []
    chunk_word_count = 0
    chunk_start_time = words[0][1] if words else 0.0
    boundary_i = 0

    for _, start, end in words:
        chunk_word_count += 1
        if boundary_i < len(boundaries) and end >= boundaries[boundary_i]:
            duration = end - chunk_start_time
            if duration > 0:
                chunk_rates.append(chunk_word_count / duration)
            chunk_word_count = 0
            chunk_start_time = end
            boundary_i += 1

    if chunk_word_count > 0 and words:
        duration = words[-1][2] - chunk_start_time
        if duration > 0:
            chunk_rates.append(chunk_word_count / duration)

    if len(chunk_rates) >= 2:
        mean_rate = sum(chunk_rates) / len(chunk_rates)
        variance = sum((r - mean_rate) ** 2 for r in chunk_rates) / len(chunk_rates)
        speech_rate_cv = round((variance ** 0.5) / mean_rate, 3) if mean_rate > 0 else 0.0
    else:
        # 침묵으로 끊긴 덩어리가 1개 이하면 "변동"이라는 개념 자체가 성립 안 함
        speech_rate_cv = 0.0

    # --- 인접 반복: 바로 옆 단어와 똑같은 토큰이 연달아 나온 횟수
    repetition_count = 0
    for i in range(1, len(words)):
        prev_text = words[i - 1][0].rstrip(".,!?~ㅋㅎ")
        curr_text = words[i][0].rstrip(".,!?~ㅋㅎ")
        if prev_text and prev_text == curr_text:
            repetition_count += 1

    # --- 종합 점수 (0~100, 높을수록 머뭇거림 심함): 침묵 비율 50% + 속도 변동 30% + 반복 20%
    silence_ratio = 0.0
    if speech_duration_sec > 0:
        silence_ratio = sum(s["duration"] for s in silence_segments) / speech_duration_sec

    hesitation_score = round(
        min(100.0, (
            min(silence_ratio, 1.0) * 50
            + min(speech_rate_cv, 1.0) * 30
            + min(repetition_count / 5, 1.0) * 20
        )),
        1,
    )

    return {
        "speech_rate_cv": speech_rate_cv,
        "repetition_count": repetition_count,
        "hesitation_score": hesitation_score,
    }


# ---------------------------------------------------------------------------
# 3-3. 말하기 점수 변환식 — process_answer() 결과를 리포트의 speech 축 점수(0~100)로
#
# 설계: hesitation_score(0~100, 높을수록 머뭇거림 심함)를 뒤집은 값을 출발점으로 삼고,
# 마무리 지표(trailing_off · silent_ending)에서 감점하는 방식.
#
#   100 - hesitation_score               출발점
#   - trailing_off면 감점                종결어미 없이 답변이 흐지부지 끝남
#   - silent_ending이면 추가 감점         끝맺지 못한 데다 뒤에 긴 침묵까지 있음
#
# is_timeout=True(시간 초과로 답변이 강제 종료된 경우)는 마무리 감점에서 제외한다.
# 답변이 안 끝난 게 화자 탓이 아니라 시간이 끝난 탓이기 때문이다. 이 경우
# hesitation_score 기반 점수만 그대로 쓴다.
#
# 알려진 한계: 감점 폭(8/7)은 3명 데이터로 임시로 잡은 값이라 실제 서비스
# 데이터가 쌓이면 앵커 답변 세트로 재조정이 필요하다.
# ---------------------------------------------------------------------------

def speech_score(metrics: dict) -> int:
    """process_answer()가 반환한 dict를 받아 speech 축 점수(0~100)로 변환한다.

    입력은 process_answer()의 반환 dict를 그대로 넘기면 된다
    (hesitation_score · trailing_off · silent_ending · is_timeout 키를 읽는다).
    다른 키가 없거나 dict 형태만 맞으면 되므로, 캐시에서 꺼낸 과거 결과를
    그대로 넘겨도 동작한다.
    """
    hesitation = float(metrics.get("hesitation_score", 0.0))
    score = 100.0 - hesitation

    if not metrics.get("is_timeout", False):
        if metrics.get("trailing_off"):
            score -= SPEECH_TRAILING_OFF_PENALTY
        if metrics.get("silent_ending"):
            score -= SPEECH_SILENT_ENDING_PENALTY

    return max(0, min(100, round(score)))


def word_timings(segments) -> list[dict]:
    """단어마다 [말한 글자, 시작 초, 끝 초]. 오디오 시작 기준이다."""
    out = []
    for seg in segments:
        for w in getattr(seg, "words", None) or []:
            text = (w.word or "").strip()
            if text:
                out.append({"text": text, "start": round(float(w.start), 2),
                            "end": round(float(w.end), 2)})
    return out


# ---------------------------------------------------------------------------
# 4. 캐시 — session_id + question_id
# (세션 중 꼬리질문 생성용으로 이미 전사한 걸 리포트가 재사용)
# ---------------------------------------------------------------------------

def _cache_path(session_id: str, question_id: str) -> str:
    os.makedirs(CACHE_DIR, exist_ok=True)
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
    fluency_metrics = compute_fluency_metrics(
        segments, speech_metrics["silence_segments"], speech_metrics["speech_duration_sec"]
    )

    result = {
        "session_id": session_id,
        "question_id": question_id,
        "is_timeout": bool(is_timeout),
        **speech_metrics,
        **ending_metrics,
        **fluency_metrics,
        # 단어별 시간. 리포트가 「답변의 몇 초 부분」을 근거로 짚을 때 쓴다.
        # word_timestamps=True로 이미 계산하던 값을 버리지 않고 담기만 한다.
        "words": word_timings(segments),
    }

    cache_set(session_id, question_id, result)
    return result
