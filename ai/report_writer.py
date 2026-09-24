"""리포트의 글 칸을 채운다 — 내용 근거 · 개선 답변 · 기업 코멘트.

점수는 이미 나와 있다(내용 · 말하기 · 시선). 여기서는 그 점수와 답변 전사를 보고
사용자가 읽을 문장을 만든다. Claude를 리포트당 **한 번만** 부른다.

    내용 근거      문항마다 강점 · 약점. 답변에서 그대로 따온 구절과 함께
    개선 답변      내용 점수가 낮은 문항 최대 2개. 무엇을 더하면 좋은지
    기업 코멘트    인재상을 보냈을 때만. 인재상에 비춰 드러난 것과 부족한 것

**답변에 없는 말을 지어내지 않는다.** 따온 구절은 전사에 실제로 있어야 하고,
없으면 버린다. 개선 답변의 원문 발췌는 사용자가 자기 말로 읽는 부분이라
하나라도 지어내면 신뢰가 무너진다.

**실패해도 리포트는 나간다.** 점수는 이미 있고 이건 설명이라, 실패하면 빈 칸으로 둔다.
가짜 문장을 채우지 않는다.

「답변 몇 초 부분」은 전사의 단어별 시간에서 찾는다(locate). 단어 시간이 없는
예전 전사면 답변 전체 구간으로 표시한다.
"""
import hashlib
import json
import logging
import re
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Literal, Optional

from pydantic import BaseModel, Field

logger = logging.getLogger("cue.ai.report_writer")

MAX_TOKENS = 4000
MAX_EVIDENCE_PER_QUESTION = 2
MAX_IMPROVED = 2
LABEL_MAX = 12


# ---------------------------------------------------------------------------
# 입력 · 출력 모양
# ---------------------------------------------------------------------------


@dataclass
class Answer:
    """문항 하나. 되묻기 답변은 원 문항 아래에 붙는다."""

    question_id: str
    question: str
    category: Optional[str]
    kind: str                 # "question" 주질문 · "followup" 꼬리질문
    text: str                 # 원 답변 전사
    content_score: int
    duration_sec: float = 0.0
    words: Optional[list] = None
    reasks: list = field(default_factory=list)   # [Answer] 되묻기 답변


class _Evidence(BaseModel):
    question_id: str
    kind: Literal["strength", "weakness"]
    label: str = Field(description=f"{LABEL_MAX}자 이내 분류명. 예: 구체적 수치, 결론 없음")
    comment: str = Field(description="답변자에게 하는 한 문장. 존댓말")
    quote: str = Field(description="답변 전사에서 그대로 복사한 연속 구절. 5~40자")


class _Improved(BaseModel):
    question_id: str
    quote: str = Field(description="고칠 부분. 답변 전사에서 그대로 복사한 연속 구절. 5~60자")
    suggestion: str = Field(description="무엇을 더하거나 바꾸면 좋은지 1~2문장. 존댓말")


class _Written(BaseModel):
    evidence: list[_Evidence]
    improved_answers: list[_Improved]
    company_comment: Optional[str] = Field(
        default=None, description="인재상이 주어졌을 때만 1~2문장. 없으면 null"
    )


@dataclass
class Placed:
    """시간까지 붙인 결과. report_dummy가 계약서 모양으로 옮긴다."""

    question_id: str
    t_start: float
    t_end: float
    kind: str = ""
    label: str = ""
    comment: str = ""
    excerpt: str = ""
    suggestion: str = ""


@dataclass
class Written:
    evidence: list = field(default_factory=list)          # [Placed]
    improved_answers: list = field(default_factory=list)  # [Placed]
    company_comment: Optional[str] = None


# ---------------------------------------------------------------------------
# 프롬프트
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """당신은 모의면접 리포트의 코멘트를 씁니다.
점수는 이미 매겨져 있습니다. 점수를 바꾸거나 새로 매기지 않습니다.
답변을 읽고, 답변자가 다음 면접에서 바로 고칠 수 있게 짚어 줍니다.

규칙
- quote는 답변 전사에서 **글자 그대로 복사**합니다. 띄어쓰기 · 조사 · 어미까지 같아야 합니다.
  요약하거나 다듬으면 안 됩니다. 되묻기 답변에서 따와도 됩니다.
- 답변에 없는 경험 · 수치 · 사실을 지어내지 않습니다.
  「예를 들어 ~처럼 수치를 넣어 보세요」처럼 방향만 제시합니다.
- evidence는 문항마다 최대 2개입니다. 강점이 없으면 약점만, 약점이 없으면 강점만 씁니다.
  내용 점수가 높은 문항에 억지로 약점을 만들지 않고, 낮은 문항에 억지로 칭찬하지 않습니다.
- label은 12자 이내 명사형입니다. 예: 구체적 수치, 결론 없음, 역할 불분명
- improved_answers는 내용 점수가 낮은 문항부터 최대 2개입니다. 모든 문항이 80점 이상이면 비워 둡니다.
- company_comment는 인재상이 주어졌을 때만 씁니다. 인재상의 가치 중 답변에서 드러난 것과
  더 보여주면 좋을 것을 1~2문장으로 씁니다. 인재상이 없으면 null입니다.
- 모든 문장은 답변자에게 말하는 존댓말입니다. 말투 · 발음 · 자신감은 다른 축에서 보므로 다루지 않습니다."""


def _prompt(persona: str, job_role: str, company_profile: Optional[str],
            answers: list[Answer]) -> str:
    lines = [f"직무: {job_role}", f"면접관: {'압박형' if persona == 'pressure' else '친절형'}", ""]
    if company_profile:
        lines += ["인재상:", company_profile.strip(), ""]
    else:
        lines += ["인재상: 없음", ""]
    for a in answers:
        kind = "주질문" if a.kind == "question" else "꼬리질문"
        lines.append(f"[{a.question_id}] {kind} · {a.category or '-'} · 내용 점수 {a.content_score}")
        lines.append(f"질문: {a.question}")
        lines.append(f"답변: {a.text or '(말 없음)'}")
        for r in a.reasks:
            lines.append(f"[{r.question_id}] 되묻기: {r.question}")
            lines.append(f"답변: {r.text or '(말 없음)'}")
        lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 실행
# ---------------------------------------------------------------------------

# 재시도 때 요청하지 않은 축은 다시 계산하지 않는다. 입력이 같으면 결과를 재사용한다.
_CACHE: "OrderedDict[str, Written]" = OrderedDict()
CACHE_MAX = 2000


def _cache_key(session_id, persona, job_role, company_profile, answers) -> str:
    payload = json.dumps({
        "s": session_id, "p": persona, "j": job_role, "c": company_profile,
        "a": [(a.question_id, a.question, a.text, a.content_score,
               [(r.question_id, r.text) for r in a.reasks]) for a in answers],
    }, ensure_ascii=False, sort_keys=True)
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


def reset() -> None:
    _CACHE.clear()


def write(
    session_id: str,
    persona: str,
    job_role: str,
    company_profile: Optional[str],
    answers: list[Answer],
    call=None,
) -> Written:
    """코멘트를 만든다. 실패하면 빈 Written. 예외를 올리지 않는다.

    call은 테스트가 Claude 대신 꽂는 자리다. (system, user) → _Written
    """
    if not answers:
        return Written()

    key = _cache_key(session_id, persona, job_role, company_profile, answers)
    if key in _CACHE:
        _CACHE.move_to_end(key)
        return _CACHE[key]

    try:
        raw = (call or _call_claude)(SYSTEM_PROMPT, _prompt(persona, job_role, company_profile, answers))
    except Exception as e:
        logger.warning("리포트 코멘트 생성 실패 — 빈 칸으로 둡니다 (%s)", type(e).__name__)
        return Written()

    written = _place(raw, answers, company_profile)
    _CACHE[key] = written
    while len(_CACHE) > CACHE_MAX:
        _CACHE.popitem(last=False)
    return written


def _call_claude(system: str, user: str) -> _Written:
    from ai import llm

    response = llm._client().messages.parse(
        model=llm.model(),
        max_tokens=MAX_TOKENS,
        system=system,
        messages=[{"role": "user", "content": user}],
        output_config={"effort": "low"},
        output_format=_Written,
    )
    llm._log_usage(response, 1, kind="리포트 코멘트")
    if response.parsed_output is None:
        raise ValueError(f"코멘트를 해석하지 못했습니다 (stop_reason={response.stop_reason})")
    return response.parsed_output


# ---------------------------------------------------------------------------
# 검증과 시간 찾기
# ---------------------------------------------------------------------------


def _place(raw: _Written, answers: list[Answer], company_profile: Optional[str]) -> Written:
    """Claude 결과를 믿지 않고 하나씩 확인한다.

    evidence      문항이 없으면 버린다. 구절을 못 찾으면 답변 전체 구간으로 둔다
    개선 답변      구절을 못 찾으면 버린다. 사용자가 자기 말로 읽는 부분이라 지어내면 안 된다
    기업 코멘트    인재상을 안 보냈으면 무시한다
    """
    by_id = {a.question_id: a for a in answers}
    out = Written()

    per_question: dict[str, int] = {}
    for e in raw.evidence:
        owner = by_id.get(e.question_id) or _owner_of(e.question_id, answers)
        if owner is None or not e.comment.strip():
            continue
        if per_question.get(owner.question_id, 0) >= MAX_EVIDENCE_PER_QUESTION:
            continue
        spot = _find(e.quote, owner)
        qid, t0, t1 = spot if spot else (owner.question_id, 0.0, float(owner.duration_sec))
        out.evidence.append(Placed(
            question_id=qid, t_start=t0, t_end=t1, kind=e.kind,
            label=e.label.strip()[:LABEL_MAX], comment=e.comment.strip(),
        ))
        per_question[owner.question_id] = per_question.get(owner.question_id, 0) + 1

    for imp in raw.improved_answers:
        if len(out.improved_answers) >= MAX_IMPROVED:
            break
        owner = by_id.get(imp.question_id) or _owner_of(imp.question_id, answers)
        if owner is None or not imp.suggestion.strip():
            continue
        spot = _find(imp.quote, owner)
        if spot is None:
            continue   # 원문에 없는 발췌는 내보내지 않는다
        qid, t0, t1 = spot
        out.improved_answers.append(Placed(
            question_id=qid, t_start=t0, t_end=t1,
            excerpt=imp.quote.strip(), suggestion=imp.suggestion.strip(),
        ))

    if company_profile and raw.company_comment and raw.company_comment.strip():
        out.company_comment = raw.company_comment.strip()
    return out


def _owner_of(question_id: str, answers: list[Answer]) -> Optional[Answer]:
    """되묻기 번호로 왔으면 원 문항을 찾는다."""
    for a in answers:
        if any(r.question_id == question_id for r in a.reasks):
            return a
    return None


def _find(quote: str, owner: Answer):
    """구절이 원 답변 · 되묻기 답변 중 어디에 있는지 찾는다. (question_id, t0, t1) 또는 None."""
    for ans in [owner, *owner.reasks]:
        if not _contains(ans.text, quote):
            continue
        spot = locate(quote, ans.words)
        if spot:
            return (ans.question_id, *spot)
        # 글자는 있는데 단어 시간이 없다(예전 전사) — 그 답변 전체 구간
        return (ans.question_id, 0.0, float(ans.duration_sec))
    return None


_KEEP = re.compile(r"[0-9A-Za-z가-힣]")


def _norm(text: str) -> str:
    """띄어쓰기 · 문장부호를 뺀다. 전사와 인용이 그 부분에서 자주 어긋난다."""
    return "".join(ch.lower() for ch in text if _KEEP.match(ch))


def _contains(text: str, quote: str) -> bool:
    q = _norm(quote)
    return bool(q) and q in _norm(text)


def locate(quote: str, words: Optional[list]) -> Optional[tuple[float, float]]:
    """구절이 말해진 구간(초). 단어 시간이 없거나 못 찾으면 None.

    띄어쓰기와 문장부호를 무시하고 글자 단위로 맞춘다. Whisper는 띄어쓰기를
    제멋대로 넣고, Claude는 인용할 때 문장부호를 바꾸기 때문이다.
    """
    if not words or not quote:
        return None
    q = _norm(quote)
    if not q:
        return None

    chars: list[str] = []
    owner: list[int] = []          # 글자마다 몇 번째 단어인가
    for i, w in enumerate(words):
        for ch in _norm(str(w.get("text", ""))):
            chars.append(ch)
            owner.append(i)
    joined = "".join(chars)

    at = joined.find(q)
    if at < 0:
        return None
    first, last = owner[at], owner[at + len(q) - 1]
    start = float(words[first]["start"])
    end = float(words[last]["end"])
    if end < start:
        return None
    return (round(start, 1), round(end, 1))


__all__ = ["Answer", "Placed", "Written", "locate", "reset", "write"]
