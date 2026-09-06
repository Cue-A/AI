"""Claude로 주질문을 생성한다.

주질문은 이력서만 보고 만들 수 있어 세션 시작 시 한 번에 뽑는다. (설계 문서 1장)
꼬리질문·되묻기·verdict 판정은 직전 답변 텍스트가 필요하므로 STT가 붙는 3주차 몫이다.

AI_MODE가 dummy면 이 모듈을 부르지 않는다. ai/dummy.py의 고정 문장이 그대로 나간다.
"""
import logging
import os
from typing import Optional

import anthropic
from pydantic import BaseModel, Field

from ai.resume import Resume
from ai.schemas import CATEGORIES, Category, Difficulty, Persona

logger = logging.getLogger("cue.ai.llm")

MAX_TOKENS = 8000

# 주질문 생성은 이력서를 읽고 정해진 카테고리·난이도에 맞춰 문장을 만드는
# 잘 정의된 작업이라, Sonnet으로도 품질이 나올 가능성이 높다.
# 확실히 하려면 같은 이력서로 두 모델을 돌려 눈으로 비교한다 (scripts/compare_models.py).
#
#   claude-sonnet-5   $2 / $10 per 1M   기본
#   claude-opus-5     $5 / $25 per 1M   품질이 아쉬울 때
#   claude-haiku-4-5  $1 /  $5 per 1M   배선만 확인할 때
DEFAULT_MODEL = "claude-sonnet-5"

# 1M 토큰당 (입력, 출력) 달러. 로그에 대략적인 비용을 남기는 데 쓴다.
PRICING = {
    "claude-opus-5": (5.0, 25.0),
    "claude-sonnet-5": (2.0, 10.0),
    "claude-haiku-4-5": (1.0, 5.0),
}

# 생각 깊이. thinking 토큰이 출력 요금으로 과금되므로 비용을 가장 크게 좌우한다.
#
#   low      가장 싸다. 배선 확인용
#   medium   기본값. 주질문 생성에는 이 정도면 충분하다
#   high     품질이 아쉬울 때 올린다
DEFAULT_EFFORT = "medium"


def model() -> str:
    return os.environ.get("LLM_MODEL") or DEFAULT_MODEL


def effort() -> str:
    return os.environ.get("LLM_EFFORT") or DEFAULT_EFFORT

# 난이도 정의는 설계 문서 2장, 페르소나는 9장을 그대로 옮겼다.
DIFFICULTY_GUIDE = """\
L1  사실 확인    이력서에 적힌 내용을 확인한다
                예) 그 프로젝트에서 어떤 부분을 담당하셨나요?
L2  근거 요구    선택의 이유와 판단 과정을 설명하게 한다
                예) 그 기술을 선택하신 이유는 무엇인가요?
L3  전제 흔들기  답변의 가정에 반박하고 방어하게 한다
                예) 그 방식은 요청이 몰릴 때 비용이 커지는데, 적절한 선택이었나요?"""

# 이름만 나열하면 협업·갈등과 가치관·인성이 섞인다. 무엇을 묻는 자리인지 못박는다.
CATEGORY_GUIDE = """\
지원동기      이 직무를 택한 계기와 준비 과정
직무역량      직무를 수행할 기술과 지식. 스스로 매긴 수준의 근거
프로젝트경험  무엇을 만들었고 그 안에서 어떤 설계 판단을 했는가
문제해결      막혔을 때의 접근 방식. 원인 분석과 대안 선택
협업·갈등     여러 사람과 일하며 의견이 갈렸을 때의 조율과 행동
실패·성장     뜻대로 되지 않은 일과 거기서 얻은 것
가치관·인성   일할 때 무엇을 중요하게 여기는가.
              사람 사이의 조율이 아니라 판단 기준 자체를 묻는 자리다
미래계획      앞으로 무엇을 하려 하는가. 성장 방향과 그 근거"""

PERSONA_GUIDE = {
    "friendly": "친절형입니다. 지원자가 편하게 말할 수 있도록 부드럽게 묻습니다. "
                "다만 질문 자체는 구체적이어야 합니다.",
    "pressure": "압박형입니다. 근거를 파고들고 전제를 흔듭니다. "
                "무례하지 않되 물러서지 않는 어조로 묻습니다. "
                "지원자가 이력서에 스스로 적은 한계나 아쉬웠던 점이 있다면 "
                "그것을 근거로 되물어도 좋습니다.",
}

SYSTEM_PROMPT = """\
당신은 채용 면접관입니다. 지원자의 이력서를 읽고 면접 주질문을 만듭니다.

주질문은 면접에서 새 주제를 여는 질문입니다. 꼬리질문은 여기서 만들지 않습니다.

난이도
{difficulty_guide}

카테고리 8종
{categories}

규칙
- 이력서에 실제로 적힌 내용을 근거로 묻습니다. 이력서에 없는 사실을 지어내지 않습니다.
- 지원자가 한 일에서만 질문을 만듭니다. 경력 · 프로젝트 · 활동 · 보유 역량이 대상입니다.
  이력서 파일에 개인정보(주소 · 연락처 · 생년월일), 서명란, 제출용 체크리스트,
  동의서 같은 페이지가 섞여 있어도 질문 소재로 쓰지 않습니다.
- 요청받은 카테고리와 난이도에 정확히 맞춥니다.
- 한 질문에 한 가지만 묻습니다. 두 가지를 접속사로 붙이지 않습니다.
  「어떤 대회의 어떤 상황이었나요」처럼 묻는 대상이 둘이면 하나로 줄입니다.
- 이력서를 읽으면 바로 답이 나오는 것은 묻지 않습니다.
  적힌 사실을 출발점으로 삼되, 이력서에 없는 판단이나 과정을 말하게 합니다.
- 한국어 존댓말로, 한 문장 또는 두 문장으로 씁니다.
- 질문끼리 겹치지 않게 합니다. 같은 경험에서 두 개를 만들지 말고,
  이력서의 서로 다른 부분에서 하나씩 뽑습니다.
- 번호, 머리말, 따옴표를 붙이지 않고 질문 문장만 씁니다."""


class GeneratedQuestion(BaseModel):
    category: Category = Field(description="요청받은 카테고리를 그대로 반복한다")
    difficulty: Difficulty = Field(description="요청받은 난이도를 그대로 반복한다")
    text: str = Field(description="지원자에게 그대로 읽어줄 질문 문장")


class GeneratedQuestions(BaseModel):
    questions: list[GeneratedQuestion]


class LlmError(Exception):
    """질문 생성에 실패했다. LLM_FAILED로 나간다. (계약서 8장)"""


# ---------------------------------------------------------------------------


def _client() -> anthropic.Anthropic:
    # 키는 ANTHROPIC_API_KEY 또는 ant auth login 프로필에서 온다.
    return anthropic.Anthropic()


def _slot_lines(slots: list[tuple[str, str]]) -> str:
    return "\n".join(
        f"{i}. 카테고리 {category} / 난이도 {difficulty}"
        for i, (category, difficulty) in enumerate(slots, start=1)
    )


def _instruction(job_role: str, persona: Persona, slots, company_profile) -> str:
    parts = [
        f"지원 직무는 「{job_role}」입니다.",
        PERSONA_GUIDE[persona],
    ]
    if company_profile:
        parts.append(f"지원 기업의 인재상입니다. 질문에 반영하세요.\n{company_profile}")
    parts.append(
        "아래 목록대로 주질문을 하나씩 만들어 주세요. "
        "순서와 개수를 그대로 지키고, 각 항목의 카테고리와 난이도를 그대로 반복해 주세요.\n\n"
        + _slot_lines(slots)
    )
    return "\n\n".join(parts)


def generate_main_questions(
    *,
    resume: Resume,
    job_role: str,
    persona: Persona,
    slots: list[tuple[str, str]],
    company_profile: Optional[str] = None,
) -> dict[str, str]:
    """주질문을 한 번에 생성한다.

    slots  [(카테고리, 난이도), ...] — 계획된 토픽과 예비 토픽 전부
    반환   {카테고리: 질문 문장}

    예비 토픽까지 미리 만들어 둔다. 세션 도중에 예비 토픽이 투입될 때
    다시 호출하면 그만큼 사용자를 기다리게 하기 때문이다.
    """
    if not slots:
        return {}

    unknown = [c for c, _ in slots if c not in CATEGORIES]
    if unknown:
        raise LlmError(f"알 수 없는 카테고리: {unknown}")

    system = SYSTEM_PROMPT.format(
        difficulty_guide=DIFFICULTY_GUIDE,
        categories=CATEGORY_GUIDE,
    )

    try:
        response = _client().messages.parse(
            model=model(),
            max_tokens=MAX_TOKENS,
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            messages=[{
                "role": "user",
                "content": [
                    resume.as_content_block(),
                    {"type": "text", "text": _instruction(job_role, persona, slots, company_profile)},
                ],
            }],
            output_config={"effort": effort()},
            output_format=GeneratedQuestions,
        )
    except anthropic.APIStatusError as e:
        # SDK가 429·5xx를 이미 재시도한 뒤다. 여기까지 오면 실패로 본다.
        raise LlmError(f"질문 생성 요청이 실패했습니다 (HTTP {e.status_code})") from e
    except anthropic.APIConnectionError as e:
        raise LlmError("질문 생성 서버에 연결하지 못했습니다") from e

    if response.stop_reason == "refusal":
        detail = getattr(response.stop_details, "category", None)
        raise LlmError(f"질문 생성이 거부되었습니다 (category={detail})")

    parsed = response.parsed_output
    if parsed is None:
        raise LlmError("질문 생성 결과를 해석하지 못했습니다")

    _log_usage(response, len(parsed.questions))

    return _collect(parsed, slots)


def _log_usage(response, count: int) -> None:
    """토큰 사용량을 남긴다. 비용이 예상과 맞는지 여기서 확인한다.

    출력 토큰에 thinking이 포함되며, 그것이 비용의 대부분이다.
    """
    u = response.usage
    name = model()
    in_rate, out_rate = PRICING.get(name, (0.0, 0.0))
    # 캐시 읽기는 입력 요금의 10%다
    cached = u.cache_read_input_tokens or 0
    cost = (
        u.input_tokens * in_rate + cached * in_rate * 0.1 + u.output_tokens * out_rate
    ) / 1_000_000
    logger.info(
        "주질문 %d개 · %s · effort=%s — input %s (캐시 읽기 %s) / output %s / 약 $%.4f",
        count, name, effort(), u.input_tokens, cached, u.output_tokens, cost,
    )


def _collect(parsed: GeneratedQuestions, slots: list[tuple[str, str]]) -> dict[str, str]:
    """카테고리별 질문으로 정리한다. 빠진 카테고리가 있으면 실패로 본다.

    구조화 출력이 스키마는 보장하지만 "요청한 카테고리를 다 채웠는가"까지는
    보장하지 않는다. 빠진 채로 진행하면 세션 도중에 KeyError가 난다.
    """
    by_category: dict[str, str] = {}
    for q in parsed.questions:
        text = q.text.strip()
        if text and q.category not in by_category:
            by_category[q.category] = text

    missing = [c for c, _ in slots if c not in by_category]
    if missing:
        raise LlmError(f"생성되지 않은 카테고리가 있습니다: {missing}")
    return by_category


# ---------------------------------------------------------------------------
# 모드 판별
# ---------------------------------------------------------------------------


def llm_enabled() -> bool:
    """AI_MODE가 dummy가 아니면 실제 생성을 쓴다."""
    return os.environ.get("AI_MODE", "dummy") != "dummy"
