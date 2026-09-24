"""리포트 생성 흐름.

    답변 오디오  →  전사  →  축별 분석  →  리포트 조립

전사는 세션 진행 중에 이미 한 번 했다. `ai/stt.py`가 session_id + question_id로
캐시하므로 여기서 다시 부르면 캐시가 나온다. 캐시가 없으면 그때 전사한다.
같은 답변을 두 번 전사하면 GPU 사용 시간이 두 배가 된다. (리포트 계약 7장)

AI_MODE가 dummy면 이 흐름을 타지 않는다. 오디오를 보지 않고 즉시 리포트를
만든다. 백엔드가 지금 검증하고 있는 동작이 그것이다.

축마다 실제 값이 어디서 오는지:

    content   ai/content_eval.py (D). USE_CONTENT_SCORING으로 따로 켠다
    speech    전사 때 B가 계산한 지표 → stt.speech_score. 요금 없음
    gaze      USE_GAZE=1이면 C의 L2CS 파이프라인 + gaze_score(). 끄면 해시
"""
import logging
from typing import Optional

from ai import answers as answers_mod
from ai import dummy, gaze as gaze_mod, llm, report_dummy, tasks
from ai.answers import AnswerText, MediaFetchError, SttError
from ai.gaze import GazeError
from ai.report_schemas import (
    ReportCreateRequest,
    ReportRetryRequest,
    ReportRetryTaskDone,
    ReportTaskDone,
)
from ai.tasks import BackgroundTask, TaskFailed

logger = logging.getLogger("cue.ai.report")

# 리포트 계약 3장. 질문 생성과 stage 값이 다르고 progress가 함께 온다.
REPORT_STAGES = (
    "transcribing",
    "analyzing_speech",
    "analyzing_gaze",
    "analyzing_content",
    "composing",
)


def transcribe_all(session_id: str, rows) -> dict[str, AnswerText]:
    """답변을 전부 전사한다. {question_id: AnswerText}

    텍스트만 남기지 않는다. B의 발화 지표(fluency)가 말하기 축이 되고,
    발화시간 · 어절수가 리포트의 duration_sec · word_count가 된다.

    되묻기 답변도 전사한다. 원 질문의 답변에 이어 붙여 하나로 채점하기 때문이다.
    하나라도 실패하면 전체를 실패로 본다. 일부만 전사된 리포트는
    어떤 문항이 왜 낮은지 설명할 수 없어 사용자에게 더 혼란스럽다.
    """
    out: dict[str, AnswerText] = {}
    for row in rows:
        if not row.audio_url:
            continue
        out[row.question_id] = answers_mod.transcribe(
            audio_url=row.audio_url,
            session_id=session_id,
            question_id=row.question_id,
            is_timeout=row.is_timeout,
        )
    return out


def analyze_gaze_all(rows) -> tuple[dict[str, dict], bool]:
    """영상이 있는 답변을 전부 시선 분석한다. ({question_id: 결과}, 실패 여부)

    전사와 달리 하나가 실패해도 태스크를 죽이지 않는다. 시선 실패는 부분
    리포트다. (리포트 계약 6장) 되묻기 영상은 점수에 넣지 않으므로 건너뛴다.
    USE_GAZE가 꺼져 있으면 아무것도 하지 않고, 시선 축은 더미 점수로 간다.
    """
    if not gaze_mod.gaze_enabled():
        return {}, False

    out: dict[str, dict] = {}
    for row in report_dummy.scored_answers(rows):
        if not row.video_url:
            continue
        try:
            out[row.question_id] = gaze_mod.analyze(row.video_url, row.question_id)
        except GazeError as e:
            logger.warning("시선 분석 실패 — 부분 리포트로 갑니다: %s (%s)", row.question_id, e)
            return {}, True
    return out, False


def _measure(task: BackgroundTask, session_id: str, answers, axes=REPORT_STAGES):
    """전사 → 시선 분석. 리포트와 재시도가 같이 쓴다."""
    task.set_stage("transcribing")
    try:
        heard = transcribe_all(session_id, answers)
    except MediaFetchError as e:
        # 내려받기 실패는 따로 알린다. 새 presigned URL로 다시 요청하면 풀린다
        raise TaskFailed("MEDIA_FETCH_FAILED", str(e)) from e
    except SttError as e:
        raise TaskFailed("STT_FAILED", str(e)) from e

    # 말하기 지표는 전사 때 B가 이미 계산했다. 단계만 지나간다.
    if "analyzing_speech" in axes:
        task.set_stage("analyzing_speech")

    gaze, gaze_failed = {}, False
    if "analyzing_gaze" in axes:
        task.set_stage("analyzing_gaze")
        gaze, gaze_failed = analyze_gaze_all(answers)

    transcripts = {qid: h.text for qid, h in heard.items()}
    measured = report_dummy.Measured(heard=heard, gaze=gaze, gaze_failed=gaze_failed)
    return transcripts, measured


def _build(task: BackgroundTask, session_id: str, req: ReportCreateRequest):
    """백그라운드 본체 — 전사하고 시선을 보고 리포트를 만든다."""
    transcripts, measured = _measure(task, session_id, req.answers)

    # 내용 채점은 build_report 안에서 문항마다 부른다
    task.set_stage("analyzing_content")

    task.set_stage("composing")
    return ReportTaskDone(
        status="done",
        result=report_dummy.build_report(
            session_id, req, transcripts=transcripts, measured=measured
        ),
    )


def create_report(session_id: str, req: ReportCreateRequest, task_id: str) -> Optional[object]:
    """리포트 작업을 띄운다.

    더미면 즉시 만든 결과를 주고, llm이면 백그라운드 작업을 등록한다.
    content 실패 처리는 라우터가 이미 걸렀으므로 여기까지 오지 않는다.
    """
    if not answers_mod.stt_enabled():
        return ReportTaskDone(
            status="done", result=report_dummy.build_report(session_id, req)
        )

    task = tasks.run(
        lambda t: _build(t, session_id, req),
        stages=REPORT_STAGES,
        with_progress=True,
    )
    dummy.store_task(task_id, task)
    return None


def _build_retry(task: BackgroundTask, session_id: str, req: ReportRetryRequest):
    """실패한 축만 다시 계산한다.

    전사 결과는 캐시에서 나오므로 재시도 비용이 낮다. (리포트 계약 7장)
    시선은 캐시가 없어 gaze를 다시 요청했을 때만 분석한다.
    """
    stages = ("analyzing_gaze",) if "gaze" in req.axes else ()
    transcripts, measured = _measure(task, session_id, req.answers, axes=stages)

    task.set_stage("composing")
    return ReportRetryTaskDone(
        status="done",
        result=report_dummy.build_retry(
            session_id, req, transcripts=transcripts, measured=measured
        ),
    )


def create_retry(session_id: str, req: ReportRetryRequest, task_id: str) -> Optional[object]:
    if not answers_mod.stt_enabled():
        return ReportRetryTaskDone(
            status="done", result=report_dummy.build_retry(session_id, req)
        )

    task = tasks.run(
        lambda t: _build_retry(t, session_id, req),
        stages=("transcribing", "analyzing_gaze", "composing"),
        with_progress=True,
    )
    dummy.store_task(task_id, task)
    return None


__all__ = ["REPORT_STAGES", "create_report", "create_retry", "transcribe_all"]
