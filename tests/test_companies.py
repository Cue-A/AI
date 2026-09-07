"""companies.json 데이터 검증.

인재상은 여러 사람이 각자 형식으로 수집한 마크다운을 변환한 것이다.
변환기가 표 헤더를 값으로 잘못 읽거나 구분자를 지표 끝에 남기는 일이 실제로 있었다.
그런 쓰레기가 들어가면 그대로 프롬프트에 실려 질문 품질이 조용히 나빠진다.

다시 변환할 일이 생기므로 여기서 막는다.
"""
import json

import pytest

from ai import companies
from ai.schemas import CompanyRecord

RECORDS = json.loads(companies.DATA_FILE.read_text(encoding="utf-8"))

# 마크다운 표의 헤더 행. 값으로 들어오면 안 된다.
HEADER_NAMES = {"필드", "name", "값", "indicators", "behavior_indicators"}


def test_전부_스키마대로_읽힌다():
    for row in RECORDS:
        CompanyRecord.model_validate(row)


def test_company_id가_겹치지_않는다():
    """겹치면 뒤엣것이 조용히 무시된다."""
    ids = [r["company_id"] for r in RECORDS]
    assert len(ids) == len(set(ids)), [i for i in ids if ids.count(i) > 1]


@pytest.mark.parametrize("record", RECORDS, ids=[r["company_id"] for r in RECORDS])
def test_표_헤더가_핵심가치로_들어오지_않았다(record):
    """변환기가 다음 기업의 필드 표를 물고 들어온 적이 있다."""
    for value in record["core_values"]:
        assert value["name"] not in HEADER_NAMES, record["company_id"]


@pytest.mark.parametrize("record", RECORDS, ids=[r["company_id"] for r in RECORDS])
def test_지표에_표_구분자가_남아_있지_않다(record):
    """표 셀이 여러 줄로 쪼개지면 닫는 | 가 지표 끝에 붙어 들어왔다."""
    for value in record["core_values"]:
        indicator = value.get("indicator")
        if indicator:
            assert not indicator.rstrip().endswith("|"), f"{record['company_id']} {value['name']}"
            assert indicator.strip() == indicator


@pytest.mark.parametrize("record", RECORDS, ids=[r["company_id"] for r in RECORDS])
def test_이름이_비어_있지_않다(record):
    for value in record["core_values"]:
        assert value["name"].strip()


def test_핵심가치가_없으면_인재상을_넘기지_않는다():
    """verified가 true여도 수집이 덜 된 기업이 있다.

    그 상태로 프로필을 만들면 회사 이름만 있고 내용이 없는 문장이 나가서
    LLM이 빈칸을 스스로 채운다. 아예 None을 주는 편이 안전하다.
    """
    empty = [r["company_id"] for r in RECORDS if not r["core_values"]]
    for company_id in empty:
        assert companies.profile_for(company_id, "백엔드 개발") is None
