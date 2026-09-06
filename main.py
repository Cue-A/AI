"""Cue AI 서버 — 더미 진입점.

LLM · STT · TTS 없이 고정 응답을 반환하되, 세션 구성 로직은 실제로 돌린다.
자세한 것은 CLAUDE.md와 docs/ 아래 계약서를 참고한다.
"""
import logging
import os

from fastapi import FastAPI

from ai.errors import register_error_handlers
from ai.report_router import router as report_router
from ai.router import DEFAULT_SECRET, SECRET_ENV, router

logger = logging.getLogger("cue.ai")

AI_MODE = os.environ.get("AI_MODE", "dummy")

app = FastAPI(
    title="Cue AI 서버 (dummy)",
    description=(
        "AI 모의면접 서비스 Cue의 AI 파트 더미 서버. "
        "질문 텍스트와 음성은 고정값이고, 세션 구성 로직은 실제로 돌아간다."
    ),
    version="0.1.0",
)

register_error_handlers(app)
app.include_router(router)
app.include_router(report_router)

if not os.environ.get(SECRET_ENV):
    if AI_MODE != "dummy":
        # 더미가 아닌 모드는 실제 서비스다. 기본 시크릿으로 뜨면 누구나 호출할 수 있다.
        raise RuntimeError(
            f"{SECRET_ENV}가 설정되지 않았습니다. "
            f"AI_MODE={AI_MODE}에서는 기본값 {DEFAULT_SECRET!r}을 쓸 수 없습니다."
        )
    logger.warning(
        "%s가 설정되지 않아 기본값 %r을 사용합니다. "
        "더미라서 기동은 하지만, 배포할 때는 반드시 설정하세요.",
        SECRET_ENV,
        DEFAULT_SECRET,
    )


# ---------------------------------------------------------------------------
# 헬스체크 — 시크릿 헤더 검증에서 제외된다
# ---------------------------------------------------------------------------


@app.get("/health")
def health() -> dict:
    """프로세스가 살아 있는가."""
    return {"status": "ok", "mode": AI_MODE}


@app.get("/ready")
def ready() -> dict:
    """요청을 받을 준비가 되었는가.

    더미에는 로딩할 모델이 없으므로 항상 준비 완료다.
    실제 모드에서는 여기서 모델 로딩 여부를 확인하게 된다.
    """
    return {"status": "ready", "mode": AI_MODE}
