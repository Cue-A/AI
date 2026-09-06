"""테스트 공통 설정.

시크릿을 고정하고 매 테스트마다 메모리 보관소를 비운다.
"""
import os

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
