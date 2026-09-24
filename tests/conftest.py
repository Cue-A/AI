"""테스트 공통 설정.

시크릿을 고정하고 매 테스트마다 메모리 보관소를 비운다.
"""
import os

# 개발자 각자의 .env를 읽지 않는다. 읽으면 누구 컴퓨터에서는 통과하고
# 누구 컴퓨터에서는 실패하는 테스트가 된다. 실제 키가 있는 사람의 컴퓨터에서
# AI_MODE=llm이 새어 들어오면 테스트가 진짜 API를 부를 수도 있다.
os.environ["CUE_SKIP_DOTENV"] = "1"

# 내용 채점은 문항마다 실제 API를 부른다. AI_MODE=llm으로 도는 테스트가
# 여럿이라 막아두지 않으면 테스트를 돌릴 때마다 요금이 나간다.
# 채점기를 시험하는 테스트는 report_dummy.CONTENT_SCORER에 가짜를 직접 꽂는다.
os.environ["USE_CONTENT_SCORING"] = "0"

import pytest

os.environ.setdefault("CUEANDA_SHARED_SECRET", "test-secret")

SECRET = os.environ["CUEANDA_SHARED_SECRET"]
AUTH = {"X-Cueanda-Secret": SECRET}


@pytest.fixture(autouse=True)
def _no_real_claude(monkeypatch):
    """테스트 중에는 어떤 경로로도 진짜 Claude를 부르지 않는다.

    스위치(USE_CONTENT_SCORING, AI_MODE)만으로 막으면 새 기능이 스위치를 빠뜨렸을 때
    API 키가 있는 컴퓨터에서 테스트마다 요금이 나간다. 클라이언트를 만드는 곳을
    막아 두면 빠뜨려도 요금 대신 테스트 실패로 드러난다.
    Claude를 흉내 내는 테스트는 llm._client를 직접 가짜로 바꾼다.
    """
    from ai import llm

    def forbidden():
        raise RuntimeError("테스트에서 진짜 Claude를 부르려 했습니다. 가짜를 꽂으세요")

    monkeypatch.setattr(llm, "_client", forbidden)


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
