"""운영 제약 — 배포할 때 사고가 나는 지점들.

더미 서버라도 실제로 배포해서 백엔드가 붙기 때문에, 아래 세 가지는
"더미라서 괜찮다"가 아니라 그냥 문제다.

  1. 다중 워커    세션이 프로세스 메모리에 있어 워커를 늘리면 절반이 유실된다
  2. 기본 시크릿  환경변수를 안 넣고 배포하면 누구나 호출할 수 있다
  3. 메모리 증가  보관소에 상한이 없으면 오래 띄운 서버가 계속 커진다
"""
import io
import os
import re
from pathlib import Path

import pytest

from ai import dummy, report_dummy

ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# 1. 다중 워커 — 컨테이너가 워커를 1개로 고정하는가
# ---------------------------------------------------------------------------


def test_Dockerfile은_워커를_1개로_고정한다():
    """세션이 프로세스 메모리에 있어 워커를 늘리면 SESSION_NOT_FOUND가 난다.

    워커 2개로 같은 task_id를 10회 폴링했을 때 6회가 404였다.
    인스턴스를 늘리려면 공유 저장소를 먼저 붙여야 한다.
    """
    text = io.open(ROOT / "Dockerfile", encoding="utf-8").read()
    cmds = re.findall(r'^CMD \[.*uvicorn.*\]$', text, re.M)

    assert len(cmds) == 2, f"dummy · full 두 스테이지의 CMD를 찾지 못했다: {cmds}"
    for cmd in cmds:
        assert '"--workers", "1"' in cmd, cmd


def test_compose는_레플리카를_늘리지_않는다():
    text = io.open(ROOT / "docker-compose.yml", encoding="utf-8").read()
    assert "replicas" not in text, "레플리카를 늘리면 세션이 유실된다"
    assert "deploy:" not in text


# ---------------------------------------------------------------------------
# 2. 기본 시크릿 — 더미가 아닌 모드에서는 기동을 막는가
# ---------------------------------------------------------------------------


def _reload_main(monkeypatch, *, ai_mode, secret):
    """환경변수를 바꿔 main 모듈을 다시 불러온다."""
    import importlib
    import sys

    monkeypatch.setenv("AI_MODE", ai_mode)
    if secret is None:
        monkeypatch.delenv("CUEANDA_SHARED_SECRET", raising=False)
    else:
        monkeypatch.setenv("CUEANDA_SHARED_SECRET", secret)

    sys.modules.pop("main", None)
    return importlib.import_module("main")


@pytest.mark.parametrize("ai_mode", ["full", "production", "staging"])
def test_더미가_아닌데_시크릿이_없으면_기동에_실패한다(monkeypatch, ai_mode):
    """조용히 dummy-secret으로 뜨면 누구나 호출할 수 있다."""
    with pytest.raises(RuntimeError) as e:
        _reload_main(monkeypatch, ai_mode=ai_mode, secret=None)
    assert "CUEANDA_SHARED_SECRET" in str(e.value)


def test_더미_모드는_시크릿이_없어도_뜬다(monkeypatch):
    """백엔드가 docker run 한 줄로 바로 붙을 수 있어야 한다. 대신 경고를 남긴다."""
    main = _reload_main(monkeypatch, ai_mode="dummy", secret=None)
    assert main.app is not None


def test_시크릿이_있으면_어느_모드든_뜬다(monkeypatch):
    for mode in ("dummy", "full"):
        main = _reload_main(monkeypatch, ai_mode=mode, secret="real-secret")
        assert main.app is not None


@pytest.fixture(autouse=True)
def _restore_main(monkeypatch):
    """이 파일이 main을 다시 불러오므로, 끝나고 원래 상태로 되돌린다."""
    yield
    import importlib
    import sys

    os.environ["AI_MODE"] = "dummy"
    os.environ["CUEANDA_SHARED_SECRET"] = "test-secret"
    sys.modules.pop("main", None)
    importlib.import_module("main")


# ---------------------------------------------------------------------------
# 3. 메모리 증가 — 보관소에 상한이 있는가
# ---------------------------------------------------------------------------


def test_보관소_상한이_지켜진다():
    store = {}
    for i in range(30):
        store[f"k{i}"] = i
        dummy.evict_oldest(store, 10)
        assert len(store) <= 10

    # 오래된 것부터 지워지므로 최근 10개가 남는다
    assert list(store) == [f"k{i}" for i in range(20, 30)]


def test_세션이_상한을_넘으면_오래된_것부터_지워진다(monkeypatch):
    dummy.reset()
    monkeypatch.setattr(dummy, "MAX_SESSIONS", 5)

    ids = []
    for _ in range(8):
        session = dummy.create_session(question_count=3, persona="friendly")
        ids.append(session.session_id)

    assert len(dummy.SESSIONS) == 5
    assert ids[0] not in dummy.SESSIONS      # 가장 오래된 세션은 정리됐다
    assert ids[-1] in dummy.SESSIONS         # 최근 세션은 살아 있다
    dummy.reset()


def test_지워진_세션에_답변하면_SESSION_NOT_FOUND(client, auth, monkeypatch):
    """재배포했을 때와 같은 상황이라 백엔드 처리 방식이 동일하다."""
    monkeypatch.setattr(dummy, "MAX_SESSIONS", 2)

    first = client.post(
        "/ai/sessions", headers=auth,
        json={"resume_file_url": "u", "job_role": "백엔드 개발", "persona": "friendly"},
    ).json()

    # 상한을 넘길 만큼 새 세션을 만든다
    for _ in range(3):
        client.post(
            "/ai/sessions", headers=auth,
            json={"resume_file_url": "u", "job_role": "백엔드 개발", "persona": "friendly"},
        )

    res = client.post(
        f"/ai/sessions/{first['session_id']}/answers", headers=auth,
        json={"question_id": "q_1", "audio_url": "u", "video_url": None, "is_timeout": False},
    )
    assert res.status_code == 404
    assert res.json()["error_code"] == "SESSION_NOT_FOUND"


def test_태스크도_상한을_지킨다(client, auth, monkeypatch):
    monkeypatch.setattr(dummy, "MAX_TASKS", 3)
    for _ in range(6):
        client.post(
            "/ai/sessions", headers=auth,
            json={"resume_file_url": "u", "job_role": "백엔드 개발", "persona": "friendly"},
        )
    assert len(dummy.TASKS) <= 3


def test_멱등성_키도_상한을_지킨다(monkeypatch):
    report_dummy.reset()
    monkeypatch.setattr(report_dummy, "MAX_IDEMPOTENCY_KEYS", 4)
    for i in range(10):
        report_dummy.remember(f"rpt_{i}", f"task_r{i}")
    assert len(report_dummy.IDEMPOTENCY) == 4
    assert "rpt_9" in report_dummy.IDEMPOTENCY
    assert "rpt_0" not in report_dummy.IDEMPOTENCY
    report_dummy.reset()
