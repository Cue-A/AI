"""회사 목록 — 계약서 6장.

verified가 false인 회사는 AI 서버에서 걸러서 내보낸다.
미확인 데이터가 서비스에 노출되지 않는 것이 자동으로 보장된다.
"""
import json
from functools import lru_cache
from pathlib import Path

from ai.schemas import CompanyOut, CompanyRecord

DATA_FILE = Path(__file__).parent / "data" / "companies.json"


@lru_cache(maxsize=1)
def load_records() -> tuple[CompanyRecord, ...]:
    raw = json.loads(DATA_FILE.read_text(encoding="utf-8"))
    return tuple(CompanyRecord.model_validate(item) for item in raw)


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
