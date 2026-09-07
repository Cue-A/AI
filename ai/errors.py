"""에러 응답 — 계약서 8장.

HTTP 에러 본문은 폴링 실패 응답과 같은 모양이되 status는 뺀다.
상태 코드로 이미 구분되므로 중복이기 때문이다.

    { "error_code": "UNAUTHORIZED", "message": "시크릿 헤더가 올바르지 않습니다" }

FastAPI 기본 {"detail": "..."}는 쓰지 않는다. 문자열 하나라 백엔드가 분기하려면
문자열을 파싱해야 한다.
"""
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from ai.schemas import ErrorCode, ErrorResponse


class ApiError(Exception):
    """계약서 8장의 에러 코드를 그대로 실어 나른다."""

    def __init__(self, status_code: int, error_code: ErrorCode, message: str):
        super().__init__(message)
        self.status_code = status_code
        self.error_code = error_code
        self.message = message


def error_response(status_code: int, error_code: ErrorCode, message: str) -> JSONResponse:
    body = ErrorResponse(error_code=error_code, message=message)
    return JSONResponse(status_code=status_code, content=body.model_dump())


def _is_replay_category_error(err: dict) -> bool:
    """replay_log[].category 값이 카테고리 8종과 다른 경우인가.

    이 오류만 INVALID_CATEGORY로 따로 뺀다. 422 뭉텅이에 섞으면 백엔드가
    replay_log 조립 버그를 다른 검증 오류와 구분할 수 없다.
    """
    loc = err.get("loc", ())
    return "replay_log" in loc and "category" in loc


def _is_answers_error(err: dict) -> bool:
    """리포트 요청의 answers[] 형식 오류인가. 계약서 9장의 INVALID_ANSWERS."""
    return "answers" in err.get("loc", ())


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api_error(request: Request, exc: ApiError) -> JSONResponse:
        return error_response(exc.status_code, exc.error_code, exc.message)

    @app.exception_handler(RequestValidationError)
    async def _validation_error(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        errors = exc.errors()
        if any(_is_replay_category_error(e) for e in errors):
            return error_response(
                400,
                "INVALID_CATEGORY",
                "replay_log의 category가 카테고리 8종과 일치하지 않습니다. "
                "가운뎃점(·)까지 정확히 같아야 합니다.",
            )
        if any(_is_answers_error(e) for e in errors):
            first = next(e for e in errors if _is_answers_error(e))
            where = ".".join(str(p) for p in first.get("loc", ())[1:])
            return error_response(
                400, "INVALID_ANSWERS", f"{where}: {first.get('msg', 'answers 형식 오류')}"
            )
        # 나머지 검증 오류도 422가 아니라 400으로 통일한다.
        # 백엔드가 두 가지 형식을 처리하지 않아도 되게 하기 위해서다.
        first = errors[0] if errors else {}
        where = ".".join(str(p) for p in first.get("loc", ())[1:]) or "요청 본문"
        return error_response(
            400, "INVALID_REQUEST", f"{where}: {first.get('msg', '요청 형식이 올바르지 않습니다')}"
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(
        request: Request, exc: StarletteHTTPException
    ) -> JSONResponse:
        """프레임워크가 직접 내는 오류(없는 경로, 허용되지 않는 메서드) 처리.

        계약서 8장 코드는 전부 ApiError로 명시해서 던진다. 여기 오는 것은
        계약서 범위 밖이므로 INVALID_REQUEST로 내보낸다. 없는 경로에
        SESSION_NOT_FOUND를 주면 통합할 때 원인을 잘못 짚게 된다.
        """
        return error_response(exc.status_code, "INVALID_REQUEST", str(exc.detail))
