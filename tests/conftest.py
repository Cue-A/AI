"""테스트 공통 설정.

시크릿을 고정하고 매 테스트마다 메모리 보관소를 비운다.
"""
import os

# 개발자 각자의 .env를 읽지 않는다. 읽으면 누구 컴퓨터에서는 통과하고
# 누구 컴퓨터에서는 실패하는 테스트가 된다. 실제 키가 있는 사람의 컴퓨터에서
# AI_MODE=llm이 새어 들어오면 테스트가 진짜 API를 부를 수도 있다.
os.environ["CUE_SKIP_DOTENV"] = "1"

import pytest

os.environ.setdefault("CUEANDA_SHARED_SECRET", "test-secret")

SECRET = os.environ["CUEANDA_SHARED_SECRET"]
AUTH = {"X-Cueanda-Secret": SECRET}


@pytest.fixture
def client():
    from fastapi.testclient import TestClient

    from ai import dummy, report_dummy
    from main import app

    dummy.reset()
    report_dummy.reset()
    with TestClient(app) as c:
        yield c
    dummy.reset()
    report_dummy.reset()


@pytest.fixture
def auth() -> dict:
    return dict(AUTH)
