"""회사 목록 — 계약서 6장.

verified가 false인 회사는 AI 서버에서 걸러서 내보낸다.
미확인 데이터가 서비스에 노출되지 않는 것이 자동으로 보장된다.
"""
import json
from functools import lru_cache
from pathlib import Path

from typing import Optional

from ai.schemas import CompanyOut, CompanyRecord

DATA_FILE = Path(__file__).parent / "data" / "companies.json"


@lru_cache(maxsize=1)
def load_records() -> tuple[CompanyRecord, ...]:
    raw = json.loads(DATA_FILE.read_text(encoding="utf-8"))
    return tuple(CompanyRecord.model_validate(item) for item in raw)


# job_requirements에 지원 직무가 없을 때 붙인다.
# 없으면 LLM이 "이 기업의 프론트엔드 요구역량"을 그럴듯하게 지어내고,
# 실제 그 기업 준비생이 바로 틀린 것을 알아챈다.
NO_JOB_REQUIREMENTS_NOTE = (
    "이 기업의 해당 직무 요구역량 자료는 없습니다. "
    "직무별 구체 요건을 추론하지 말고 핵심 가치만 참고하세요."
)


def find(company_id: Optional[str]) -> Optional[CompanyRecord]:
    if not company_id:
        return None
    for record in load_records():
        if record.company_id == company_id:
            return record
    return None


def _match_job(record: CompanyRecord, job_role: Optional[str]) -> Optional[list[str]]:
    """지원 직무에 해당하는 요구역량.

    job_role은 계약서상 자유 문자열이고 job_requirements의 키는 닫힌 목록이라
    정확히 맞지 않을 수 있다. 공백과 대소문자만 무시하고 맞춰보며,
    못 찾으면 None을 돌려 핵심 가치만 쓰게 한다.
    """
    if not record.job_requirements or not job_role:
        return None

    def norm(s: str) -> str:
        return s.strip().lower().replace(" ", "").replace("_", "").replace("-", "")

    wanted = norm(job_role)
    for key, items in record.job_requirements.items():
        if norm(key) == wanted:
            return items
    return None


def profile_for(
    company_id: Optional[str], job_role: Optional[str] = None
) -> Optional[str]:
    """질문 생성에 넣을 인재상 문단. 없으면 None이고 직무만으로 만든다.

    verified가 false인 기업은 쓰지 않는다. 공식 채용페이지에서 확인되지 않은
    내용으로 질문을 만들면 미확인 데이터가 서비스에 노출되는 셈이다.
    """
    record = find(company_id)
    if record is None or not record.verified or not record.core_values:
        return None

    lines = [f"{record.name} ({record.industry})", "", "핵심 가치"]
    for value in record.core_values:
        lines.append(
            f"  {value.name} — {value.indicator}" if value.indicator else f"  {value.name}"
        )

    requirements = _match_job(record, job_role)
    if requirements:
        lines += ["", f"{job_role} 직무 요구역량"]
        lines += [f"  {item}" for item in requirements]
    else:
        lines += ["", NO_JOB_REQUIREMENTS_NOTE]

    return "\n".join(lines)


def verified_companies() -> list[CompanyOut]:
    return [
        CompanyOut(
            company_id=r.company_id,
            name=r.name,
            industry=r.industry,
        )
        for r in load_records()
        if r.verified
    ]
