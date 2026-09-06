"""/ai/* 엔드포인트 — 계약서 1장.

POST  /ai/sessions                         세션 시작
POST  /ai/sessions/{session_id}/answers    답변 제출
GET   /ai/tasks/{task_id}                  작업 상태 조회 (폴링)
GET   /ai/companies                        회사 목록
POST  /ai/sessions/{session_id}/abort      세션 중단

비동기는 흉내만 낸다. 즉시 계산해서 저장해두고 GET /ai/tasks에서 꺼내준다.
Celery와 Redis는 쓰지 않는다.
"""
import os
from typing import Optional

from fastapi import APIRouter, Depends, Header

from ai import companies, dummy
from ai.errors import ApiError
from ai.schemas import (
    AbortResponse,
    AnswerSubmitRequest,
    AnswerSubmitResponse,
    CompanyOut,
    SessionCreateRequest,
    SessionCreateResponse,
)

SECRET_ENV = "CUEANDA_SHARED_SECRET"
DEFAULT_SECRET = "dummy-secret"  # 더미 서버 기본값. 배포 시 환경변수로 덮어쓴다


def expected_secret() -> str:
    return os.environ.get(SECRET_ENV) or DEFAULT_SECRET


def require_secret(
    x_cueanda_secret: Optional[str] = Header(default=None, alias="X-Cueanda-Secret"),
) -> None:
    """계약서 0-1장. 헤더가 없거나 값이 다르면 401을 반환한다.

    /health와 /ready는 예외이므로 이 의존성을 걸지 않는다.
    """
    if x_cueanda_secret != expected_secret():
        raise ApiError(401, "UNAUTHORIZED", "시크릿 헤더가 없거나 올바르지 않습니다")


router = APIRouter(prefix="/ai", dependencies=[Depends(require_secret)])


def _get_session(session_id: str) -> dummy.DummySession:
    session = dummy.SESSIONS.get(session_id)
    if session is None:
        # AI 서버 재배포 중에도 이 코드가 날 수 있다. 세션 상태가 임시 보관이라 그렇다.
        raise ApiError(404, "SESSION_NOT_FOUND", "세션이 없거나 만료되었습니다")
    return session


# ---------------------------------------------------------------------------
# 2. 세션 시작
# ---------------------------------------------------------------------------


@router.post("/sessions", status_code=202, response_model=SessionCreateResponse)
def create_session(req: SessionCreateRequest) -> SessionCreateResponse:
    if req.retry_of_session_id and not req.replay_log:
        raise ApiError(
            400,
            "INVALID_REQUEST",
            "retry_of_session_id가 있으면 replay_log를 함께 보내야 합니다",
        )

    session, first_question = dummy.create_session(
        question_count=req.question_count,
        persona=req.persona,
        replay_log=req.replay_log,
    )
    task_id = dummy.save_task(first_question)
    return SessionCreateResponse(
        session_id=session.session_id,
        task_id=task_id,
        question_total=session.question_total,
    )


# ---------------------------------------------------------------------------
# 3. 답변 제출
# ---------------------------------------------------------------------------


@router.post(
    "/sessions/{session_id}/answers",
    status_code=202,
    response_model=AnswerSubmitResponse,
)
def submit_answer(session_id: str, req: AnswerSubmitRequest) -> AnswerSubmitResponse:
    session = _get_session(session_id)

    if session.ended:
        raise ApiError(409, "SESSION_ENDED", "이미 종료된 세션입니다")

    if req.question_id != session.current_question_id:
        raise ApiError(
            400,
            "INVALID_QUESTION_ID",
            f"현재 질문은 {session.current_question_id}입니다",
        )

    result = session.answer(req.audio_url, is_timeout=req.is_timeout)
    return AnswerSubmitResponse(task_id=dummy.save_task(result))


# ---------------------------------------------------------------------------
# 4. 작업 상태 조회
# ---------------------------------------------------------------------------


@router.get("/tasks/{task_id}", response_model=None)
def get_task(task_id: str):
    """폴링. 응답 형태는 계약서 4장의 TaskDoneResponse / TaskProcessingResponse /
    TaskErrorResponse 중 하나다.

    기본값에서는 즉시 done이 된다. DUMMY_POLL_TICKS를 켜면 그 횟수만큼
    processing을 돌려준 뒤 done이 되므로, 백엔드가 폴링 루프를 검증할 수 있다.
    """
    task = dummy.read_task(task_id)
    if task is None:
        # 계약서 8장에 작업 전용 코드가 없다. 재시도 없이 세션을 정리해야 하는
        # 상황이라는 점에서 SESSION_NOT_FOUND와 처리가 같다.
        raise ApiError(404, "SESSION_NOT_FOUND", "작업이 없거나 만료되었습니다")
    return task


# ---------------------------------------------------------------------------
# 6. 회사 목록
# ---------------------------------------------------------------------------


@router.get("/companies", response_model=list[CompanyOut])
def list_companies() -> list[CompanyOut]:
    return companies.verified_companies()


# ---------------------------------------------------------------------------
# 7. 세션 중단
# ---------------------------------------------------------------------------


@router.post("/sessions/{session_id}/abort", response_model=AbortResponse)
def abort_session(session_id: str) -> AbortResponse:
    _get_session(session_id)
    # 세션 상태를 정리한다. 중단된 세션은 리포트를 생성하지 않는다.
    dummy.SESSIONS.pop(session_id, None)
    return AbortResponse(status="aborted")
