"""답변 영상을 시선 지표로 바꾼다.

    video_url  →  infra/gaze_analysis  →  응시유지율 · 회피빈도 · 회피 구간

C가 만든 프로덕션 경로(`gaze_pipeline.analyze_gaze_from_presigned_url`)를
그대로 부른다. 다운로드 → 5fps 분석 → 구간 지표 → 영상 삭제까지 그 안에서 끝난다.

**시선 분석이 실패해도 리포트는 나간다.** 리포트 계약 6장이 정한 규칙이다.

    GAZE_FAILED   gaze 축만 failed. 남은 두 축으로 재정규화한 부분 리포트

STT · TTS와 마찬가지로 스위치를 따로 둔다. L2CS는 GPU와 가중치 파일이
있어야 돌기 때문에, 켜지 않으면 시선 축은 더미 점수로 간다.

    USE_GAZE=1       실제로 분석한다
    GAZE_WEIGHTS     L2CS 가중치 파일 경로 (필수)
    GAZE_DEVICE      cuda | cpu. 기본 cuda
"""
import logging
import os

logger = logging.getLogger("cue.ai.gaze")

GAZE_ENV = "USE_GAZE"
WEIGHTS_ENV = "GAZE_WEIGHTS"
DEVICE_ENV = "GAZE_DEVICE"


def gaze_enabled() -> bool:
    return os.environ.get(GAZE_ENV, "").strip().lower() in ("1", "true", "yes", "on")


class GazeError(Exception):
    """시선 분석에 실패했다. 리포트의 GAZE_FAILED로 옮겨진다."""


def analyze(video_url: str, question_id: str) -> dict:
    """답변 영상 하나를 분석한다.

    반환은 C의 결과 dict 그대로다. gaze_maintain_ratio · aversion_frequency ·
    evidence 키를 리포트가 쓴다. 무엇이 터지든 GazeError 하나로 올린다.
    """
    weights = os.environ.get(WEIGHTS_ENV, "").strip()
    if not weights:
        raise GazeError(f"{WEIGHTS_ENV}가 비어 있습니다")

    try:
        # torch · cv2 · l2cs를 끌어오므로 쓸 때만 import한다
        from infra.gaze_analysis.gaze_pipeline import analyze_gaze_from_presigned_url

        result = analyze_gaze_from_presigned_url(
            presigned_url=video_url,
            weights_path=weights,
            question_id=question_id,
            device=os.environ.get(DEVICE_ENV, "cuda").strip() or "cuda",
        )
    except Exception as e:
        raise GazeError(f"시선 분석에 실패했습니다: {type(e).__name__}") from e

    if "gaze_maintain_ratio" not in result or "aversion_frequency" not in result:
        raise GazeError("시선 분석 결과를 해석하지 못했습니다")
    return result


__all__ = ["GazeError", "analyze", "gaze_enabled"]
