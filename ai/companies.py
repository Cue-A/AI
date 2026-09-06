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


def profile_for(company_id: Optional[str]) -> Optional[str]:
    """등록된 기업의 인재상. 없으면 None이고, 그러면 직무만으로 질문을 만든다.

    verified가 false인 기업도 조회된다. 목록에 노출하지 않을 뿐,
    이미 선택된 세션이라면 인재상은 반영하는 것이 맞다.
    """
    if not company_id:
        return None
    for record in load_records():
        if record.company_id == company_id:
            return record.profile or None
    return None


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
