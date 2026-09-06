"""질문 생성 API — 요청·응답 Pydantic 모델

원본 스펙: docs/질문생성_API계약_백엔드전달용.md
          docs/질문 유형.md 12장

계약서에 없는 필드는 추가하지 않는다. 필드명·타입·Optional 여부는 계약서가 기준이다.
"""
from typing import Literal, Optional, Union

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# 공통 리터럴
# ---------------------------------------------------------------------------

# 카테고리 8종. 가운뎃점은 U+00B7 MIDDLE DOT이며 ai/session_plan.py와 동일한 문자다.
# 다른 문자를 쓰면 INVALID_CATEGORY가 된다.
Category = Literal[
    "지원동기",
    "직무역량",
    "프로젝트경험",
    "문제해결",
    "협업·갈등",
    "실패·성장",
    "가치관·인성",
    "미래계획",
]

Difficulty = Literal["L1", "L2", "L3"]

# 계약서 4장 · 세션진행 12장. session_end는 폴링 결과에만 나온다.
QuestionType = Literal["question", "followup", "reask", "session_end"]

Persona = Literal["friendly", "pressure"]

QuestionCount = Literal[3, 6, 9]

# 계약서 4장. AI 내부 파이프라인 이름이므로 백엔드가 자체 enum으로 매핑한다.
Stage = Literal["stt", "generating", "tts"]

# 계약서 8장. INVALID_REQUEST는 계약서에 없던 코드이며,
# Pydantic 검증 오류를 422가 아닌 400 한 가지 형식으로 내보내기 위해 합의로 추가했다.
ErrorCode = Literal[
    "INVALID_REQUEST",
    "UNAUTHORIZED",
    "SESSION_NOT_FOUND",
    "SESSION_ENDED",
    "INVALID_QUESTION_ID",
    "INVALID_CATEGORY",
    "RESUME_PARSE_FAILED",
    "STT_FAILED",
    "LLM_FAILED",
    "TTS_FAILED",
    # 리포트 생성 계약 9장
    "REPORT_TOO_SHORT",
    "CONTENT_FAILED",
    "SPEECH_FAILED",
    "GAZE_FAILED",
    "MEDIA_FETCH_FAILED",
    "INVALID_ANSWERS",
]

CATEGORIES: tuple[str, ...] = (
    "지원동기",
    "직무역량",
    "프로젝트경험",
    "문제해결",
    "협업·갈등",
    "실패·성장",
    "가치관·인성",
    "미래계획",
)


# ---------------------------------------------------------------------------
# 2. 세션 시작
# ---------------------------------------------------------------------------


class ReplayLogItem(BaseModel):
    """재연습 요청에 담기는 1회차 진행 로그 한 줄. 계약서 2장.

    question  text · category · difficulty · is_spare_topic 모두 필수
    followup  difficulty만 필요. text는 새로 생성하므로 불필요
    reask     배열에서 제외한다. 재현하지 않는다
    """

    type: Literal["question", "followup"]
    text: Optional[str] = None
    category: Optional[Category] = None
    difficulty: Optional[Difficulty] = None
    is_spare_topic: Optional[bool] = None


class SessionCreateRequest(BaseModel):
    """POST /ai/sessions 요청. 계약서 2장."""

    resume_file_url: str
    job_role: str
    persona: Persona
    company_id: Optional[str] = None
    company_profile_override: Optional[str] = None
    question_count: QuestionCount = 6
    retry_of_session_id: Optional[str] = None
    # 재연습일 때만 필수다. retry_of_session_id가 있는데 이 값이 없으면 400을 반환하는데,
    # 모델 검증으로 막으면 FastAPI가 422를 내므로 라우터에서 확인한다.
    replay_log: Optional[list[ReplayLogItem]] = None
    doc_id: Optional[str] = None


class SessionCreateResponse(BaseModel):
    """202 Accepted. 첫 질문은 task_id로 폴링해서 받는다."""

    session_id: str
    task_id: str
    question_total: int


# ---------------------------------------------------------------------------
# 3. 답변 제출
# ---------------------------------------------------------------------------


class AnswerSubmitRequest(BaseModel):
    """POST /ai/sessions/{session_id}/answers 요청. 계약서 3장.

    발화 시간과 어절 수는 AI가 STT 결과에서 계산하므로 백엔드가 보내지 않는다.
    """

    question_id: str
    audio_url: str
    video_url: Optional[str] = None
    is_timeout: bool


class AnswerSubmitResponse(BaseModel):
    """202 Accepted."""

    task_id: str


# ---------------------------------------------------------------------------
# 4. 작업 상태 조회 (폴링)
# ---------------------------------------------------------------------------


class QuestionResult(BaseModel):
    """폴링 완료 결과 — 질문 · 꼬리질문 · 되묻기. 계약서 4장, 세션진행 12장.

    되묻기는 category와 difficulty만 null이며 나머지 필드는 값이 온다.
    is_spare_topic과 is_replay는 모든 응답에 포함되므로 nullable이 아니다.
    """

    type: Literal["question", "followup", "reask"]
    question_id: str
    # 되묻기가 아니면 null. 계약서 4장 질문 예시에는 표기되지 않아 기본값을 둔다.
    reask_of: Optional[str] = None
    text: str
    audio_url: Optional[str]  # TTS 실패 시 null
    category: Optional[Category]  # 되묻기는 null
    difficulty: Optional[Difficulty]  # 되묻기는 null
    question_number: int
    question_total: int
    topic_index: int
    topic_total: int
    is_spare_topic: bool
    is_replay: bool


class SessionEndResult(BaseModel):
    """폴링 완료 결과 — 세션 종료.

    total_questions는 되묻기를 제외한 실제 질문 수이며 question_total과 항상 같다.
    """

    type: Literal["session_end"]
    total_questions: int


class TaskProcessingResponse(BaseModel):
    status: Literal["processing"]
    stage: Stage


class TaskDoneResponse(BaseModel):
    status: Literal["done"]
    result: Union[QuestionResult, SessionEndResult] = Field(discriminator="type")


class TaskErrorResponse(BaseModel):
    status: Literal["error"]
    error_code: ErrorCode
    message: str


TaskStatusResponse = Union[
    TaskDoneResponse,
    TaskProcessingResponse,
    TaskErrorResponse,
]


# ---------------------------------------------------------------------------
# 6. 회사 목록
# ---------------------------------------------------------------------------


class CompanyOut(BaseModel):
    """GET /ai/companies 응답 항목. 계약서 6장.

    verified는 응답에 담지 않는다. false인 회사는 AI 서버에서 걸러서 내보낸다.
    """

    company_id: str
    name: str
    industry: str


class CompanyRecord(CompanyOut):
    """ai/data/companies.json 한 줄. 서버 내부 전용이며 응답으로 나가지 않는다."""

    verified: bool
    # 인재상. 질문 생성에만 쓰고 응답에는 담지 않는다.
    # 계약서 9장 — 인재상 내용은 AI가 보관하고 백엔드는 company_id만 들고 다닌다.
    # 아직 실제 자료가 없어 전부 null이며, null이면 직무만으로 질문을 만든다.
    profile: Optional[str] = None


# ---------------------------------------------------------------------------
# 7. 세션 중단
# ---------------------------------------------------------------------------


class AbortResponse(BaseModel):
    status: Literal["aborted"]


# ---------------------------------------------------------------------------
# 8. 에러
# ---------------------------------------------------------------------------


class ErrorResponse(BaseModel):
    """HTTP 에러 본문. 폴링 실패 응답과 같은 모양이되 status는 뺀다.

    HTTP 에러는 상태 코드로 이미 구분되므로 status가 중복이다.
    error_code는 백엔드가 분기하는 기준이므로 null이 되지 않는다.

    400  INVALID_REQUEST        일반 검증 오류
    400  INVALID_CATEGORY       카테고리 문자열 불일치
    400  INVALID_QUESTION_ID    현재 질문과 불일치
    401  UNAUTHORIZED
    404  SESSION_NOT_FOUND
    409  SESSION_ENDED
    422  RESUME_PARSE_FAILED
    500  STT_FAILED / LLM_FAILED / TTS_FAILED
    """

    error_code: ErrorCode
    message: str
