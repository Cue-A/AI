"""리포트 생성 엔드포인트 — 리포트 계약 1장.

POST  /ai/sessions/{session_id}/report          리포트 생성 요청
POST  /ai/sessions/{session_id}/report/retry    실패한 축만 재시도
POST  /ai/reports/compare                       회차 비교 · 성장 추이
GET   /ai/tasks/{task_id}                       질문 생성과 같은 엔드포인트를 쓴다

리포트 본문은 AI가 저장하지 않는다. 완성된 리포트의 보관은 백엔드가 한다.
세션 상태도 보지 않는다. 며칠 뒤에 요청해도 되도록 백엔드가 answers를 전부 보낸다.
"""
from typing import Optional

from fastapi import APIRouter, Depends, Header

from ai import dummy, report_dummy
from ai.errors import ApiError
from ai.schemas import TaskErrorResponse
from ai.report_schemas import (
    CompareRequest,
    CompareResponse,
    ReportCreateRequest,
    ReportRetryRequest,
    ReportRetryTaskDone,
    ReportTaskDone,
    TaskAccepted,
)
from ai.router import require_secret

router = APIRouter(prefix="/ai", dependencies=[Depends(require_secret)])

# 답변이 이 수보다 적으면 리포트를 만들지 않는다. (계약서 9장)
MIN_ANSWERS = 2


def require_idempotency_key(
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
) -> str:
    """계약서 2장. 권장 형식은 rpt_{session_id}_{시도번호}다.

    같은 키로 다시 요청하면 새 작업을 만들지 않고 기존 task_id를 반환한다.
    """
    if not idempotency_key:
        raise ApiError(400, "INVALID_REQUEST", "Idempotency-Key 헤더가 필요합니다")
    return idempotency_key


def _check_answers(answers) -> None:
    if not answers:
        raise ApiError(400, "INVALID_ANSWERS", "answers 배열이 비어 있습니다")
    scored = report_dummy.scored_answers(answers)
    if len(scored) < MIN_ANSWERS:
        raise ApiError(
            422,
            "REPORT_TOO_SHORT",
            f"채점할 답변이 {len(scored)}개뿐입니다. {MIN_ANSWERS}문항 이상 필요합니다",
        )


def _store(task_id: str, payload) -> None:
    # 리포트는 stage 값이 질문 생성과 다르고 progress가 붙는다 (리포트 계약 3장)
    dummy.TASKS[task_id] = dummy.pending(
        payload, dummy.REPORT_STAGES, with_progress=True
    )
    dummy.evict_oldest(dummy.TASKS, dummy.MAX_TASKS)


@router.post("/sessions/{session_id}/report", status_code=202, response_model=TaskAccepted)
def create_report(
    session_id: str,
    req: ReportCreateRequest,
    idempotency_key: str = Depends(require_idempotency_key),
) -> TaskAccepted:
    existing = report_dummy.IDEMPOTENCY.get(idempotency_key)
    if existing:
        return TaskAccepted(task_id=existing)

    _check_answers(req.answers)

    task_id = report_dummy.new_report_task_id()

    if report_dummy.content_failed(req.answers):
        # content 실패는 전체 실패다. 부분 리포트를 만들면 적절성 게이트가 돌지 않아
        # 총점을 신뢰할 수 없게 된다. 리포트 본문 없이 태스크를 error로 남긴다.
        _store(task_id, TaskErrorResponse(
            status="error",
            error_code="CONTENT_FAILED",
            message="내용 분석에 실패해 리포트를 만들지 못했습니다",
        ))
    else:
        _store(task_id, ReportTaskDone(
            status="done", result=report_dummy.build_report(session_id, req)
        ))

    report_dummy.remember(idempotency_key, task_id)
    return TaskAccepted(task_id=task_id)


@router.post(
    "/sessions/{session_id}/report/retry", status_code=202, response_model=TaskAccepted
)
def retry_report(
    session_id: str,
    req: ReportRetryRequest,
    idempotency_key: str = Depends(require_idempotency_key),
) -> TaskAccepted:
    existing = report_dummy.IDEMPOTENCY.get(idempotency_key)
    if existing:
        return TaskAccepted(task_id=existing)

    if not req.axes:
        raise ApiError(400, "INVALID_REQUEST", "재시도할 축을 지정해야 합니다")
    _check_answers(req.answers)

    result = report_dummy.build_retry(session_id, req)
    task_id = report_dummy.new_report_task_id()
    _store(task_id, ReportRetryTaskDone(status="done", result=result))
    report_dummy.remember(idempotency_key, task_id)
    return TaskAccepted(task_id=task_id)


@router.post("/reports/compare", response_model=CompareResponse)
def compare_reports(req: CompareRequest) -> CompareResponse:
    """회차 수 제한은 없다. 배열에 담긴 만큼 전부 분석한다.

    reports가 1개면 vs_previous가 null이 되고 trend 배열 길이는 1이다.
    회차가 하나뿐일 때도 오류가 아니다.
    """
    if not req.reports:
        raise ApiError(400, "INVALID_REQUEST", "reports 배열이 비어 있습니다")
    return report_dummy.build_compare(req)
