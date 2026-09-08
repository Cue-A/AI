"""리포트 생성 흐름.

    답변 오디오  →  전사  →  축별 분석  →  리포트 조립

전사는 세션 진행 중에 이미 한 번 했다. `ai/stt.py`가 session_id + question_id로
캐시하므로 여기서 다시 부르면 캐시가 나온다. 캐시가 없으면 그때 전사한다.
같은 답변을 두 번 전사하면 GPU 사용 시간이 두 배가 된다. (리포트 계약 7장)

AI_MODE가 dummy면 이 흐름을 타지 않는다. 오디오를 보지 않고 즉시 리포트를
만든다. 백엔드가 지금 검증하고 있는 동작이 그것이다.

**내용 채점은 아직 없다.** 점수가 해시로 나온다. 전사 텍스트를
`report_dummy.CONTENT_SCORER`에 꽂으면 실제 채점으로 바뀐다. D 담당이며
프롬프트 초안은 `docs/내용채점_프롬프트_초안.md`에 있다.
"""
import logging
from typing import Optional

from ai import answers as answers_mod
from ai import dummy, llm, report_dummy, tasks
from ai.answers import SttError
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


def transcribe_all(session_id: str, rows) -> dict[str, str]:
    """답변을 전부 전사한다. {question_id: 텍스트}

    되묻기 답변도 전사한다. 원 질문의 답변에 이어 붙여 하나로 채점하기 때문이다.
    하나라도 실패하면 전체를 실패로 본다. 일부만 전사된 리포트는
    어떤 문항이 왜 낮은지 설명할 수 없어 사용자에게 더 혼란스럽다.
    """
    out: dict[str, str] = {}
    for row in rows:
        if not row.audio_url:
            continue
        heard = answers_mod.transcribe(
            audio_url=row.audio_url,
            session_id=session_id,
            question_id=row.question_id,
            is_timeout=row.is_timeout,
        )
        out[row.question_id] = heard.text
    return out


def _build(task: BackgroundTask, session_id: str, req: ReportCreateRequest):
    """백그라운드 본체 — 전사하고 리포트를 만든다."""
    task.set_stage("transcribing")
    try:
        transcripts = transcribe_all(session_id, req.answers)
    except SttError as e:
        raise TaskFailed("STT_FAILED", str(e)) from e

    # 축별 분석은 B와 C가 붙인다. 지금은 단계만 지나간다.
    for stage in ("analyzing_speech", "analyzing_gaze", "analyzing_content"):
        task.set_stage(stage)

    task.set_stage("composing")
    return ReportTaskDone(
        status="done",
        result=report_dummy.build_report(session_id, req, transcripts=transcripts),
    )


def create_report(session_id: str, req: ReportCreateRequest, task_id: str) -> Optional[object]:
    """리포트 작업을 띄운다.

    더미면 즉시 만든 결과를 주고, llm이면 백그라운드 작업을 등록한다.
    content 실패 처리는 라우터가 이미 걸렀으므로 여기까지 오지 않는다.
    """
    if not llm.llm_enabled():
        return ReportTaskDone(
            status="done", result=report_dummy.build_report(session_id, req)
        )

    task = tasks.run(
        lambda t: _build(t, session_id, req),
        stages=REPORT_STAGES,
        with_progress=True,
    )
    dummy.TASKS[task_id] = task
    dummy.evict_oldest(dummy.TASKS, dummy.MAX_TASKS)
    return None


def _build_retry(task: BackgroundTask, session_id: str, req: ReportRetryRequest):
    """실패한 축만 다시 계산한다.

    전사 결과는 캐시에서 나오므로 재시도 비용이 낮다. (리포트 계약 7장)
    """
    task.set_stage("transcribing")
    try:
        transcripts = transcribe_all(session_id, req.answers)
    except SttError as e:
        raise TaskFailed("STT_FAILED", str(e)) from e

    task.set_stage("composing")
    return ReportRetryTaskDone(
        status="done",
        result=report_dummy.build_retry(session_id, req, transcripts=transcripts),
    )


def create_retry(session_id: str, req: ReportRetryRequest, task_id: str) -> Optional[object]:
    if not llm.llm_enabled():
        return ReportRetryTaskDone(
            status="done", result=report_dummy.build_retry(session_id, req)
        )

    task = tasks.run(
        lambda t: _build_retry(t, session_id, req),
        stages=("transcribing", "composing"),
        with_progress=True,
    )
    dummy.TASKS[task_id] = task
    dummy.evict_oldest(dummy.TASKS, dummy.MAX_TASKS)
    return None


__all__ = ["REPORT_STAGES", "create_report", "create_retry", "transcribe_all"]
