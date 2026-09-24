"""기업 인재상.

**기업 데이터는 백엔드가 관리한다.** (노션 최종 계약본, 백엔드와 합의)
백엔드가 인재상을 `company_profile_override`에 담아 보내고, AI는 받은 텍스트를
질문과 리포트 코멘트에 반영할 뿐 저장하지 않는다. `company_id`는 백엔드 PK를
문자열로 받은 추적용 값이라 조회에 쓰지 않는다.

`data/companies.json`은 백엔드에 넘긴 원본 데이터다. 서버는 읽지 않는다.
`profile_for`는 그 데이터를 계약서 형식의 인재상 문단으로 만드는 도구이며,
백엔드가 보낼 텍스트가 어떤 모양이어야 하는지 보여주는 기준이기도 하다.
"""
import json
from functools import lru_cache
from pathlib import Path

from typing import Optional

from ai.schemas import CompanyRecord

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

# 인재상 텍스트에 직무 요구역량이 들어 있는지 보는 표지. 계약서 형식이
# "직무 요구역량이 있으면 이어서 적는다"이고, 제목에 이 말이 들어간다.
JOB_REQUIREMENTS_MARK = "요구역량"


def guard(profile: Optional[str]) -> Optional[str]:
    """백엔드가 보낸 인재상에 추론 금지 문장을 붙인다.

    계약서: 직무 요구역량이 없으면 AI가 「추론하지 말라」는 문장을 자동으로
    붙인다. 백엔드는 인재상만 보내면 된다. 빈 문자열은 기업 미선택으로 본다.
    """
    if not profile or not profile.strip():
        return None
    text = profile.strip()
    if JOB_REQUIREMENTS_MARK in text:
        return text
    return f"{text}\n\n{NO_JOB_REQUIREMENTS_NOTE}"


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
