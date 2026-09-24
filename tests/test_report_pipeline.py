"""리포트가 답변을 실제로 듣는 흐름.

더미는 오디오를 보지 않고 즉시 리포트를 만든다. 백엔드가 지금 검증하고 있는
동작이 그것이므로 바뀌면 안 된다.

llm 모드는 전사부터 시작한다. 전사 결과는 세션 진행 중에 이미 만들어 둔 것을
캐시에서 재사용한다. 두 번 전사하면 GPU 사용 시간이 두 배가 된다.
"""
from unittest.mock import patch as mock_patch

import time
import pytest

from ai import answers, report_dummy
from ai.answers import AnswerText, SttError

# client와 auth는 conftest에 있다. 여기서 다시 만들면 매 테스트마다
# 보관소를 비우는 동작이 사라져 앞 테스트의 상태가 새어 들어온다.
from test_report import answer, key, report_body, six_answers  # noqa: F401


@pytest.fixture
def llm_mode(monkeypatch):
    monkeypatch.setenv("AI_MODE", "llm")


@pytest.fixture
def fake_stt():
    """전사를 가로챈다. GPU도 네트워크도 쓰지 않는다."""
    def transcribe(*, audio_url, session_id, question_id, is_timeout=False):
        return AnswerText(
            duration_sec=45,
            word_count=60,
            text=f"{question_id}에 대한 답변입니다. 결제 모듈을 맡았습니다.",
        )

    with mock_patch.object(answers, "transcribe", side_effect=transcribe) as stt:
        yield stt


# 백그라운드 작업이 끝나길 기다리는 시간. 횟수로 세면 컴퓨터가 바쁠 때
# 작업이 끝나기 전에 포기해서 테스트가 가끔 실패한다. 시간으로 센다.
POLL_TIMEOUT_SEC = 10.0
POLL_INTERVAL_SEC = 0.01


def poll(client, auth, task_id):
    deadline = time.monotonic() + POLL_TIMEOUT_SEC
    while True:
        res = client.get(f"/ai/tasks/{task_id}", headers=auth)
        assert res.status_code == 200, res.json()
        body = res.json()
        if body["status"] in ("done", "error"):
            return body
        if time.monotonic() > deadline:
            raise AssertionError("끝나지 않았습니다")
        time.sleep(POLL_INTERVAL_SEC)


def make(client, auth, answers_, idem=1):
    res = client.post(
        "/ai/sessions/sess_rp/report",
        headers={**auth, **key(idem)},
        json=report_body(answers_),
    )
    assert res.status_code == 202, res.json()
    return poll(client, auth, res.json()["task_id"])


# ---------------------------------------------------------------------------


def test_리포트가_답변을_전사한다(client, auth, llm_mode, fake_stt):
    body = make(client, auth, six_answers())

    assert body["status"] == "done"
    assert fake_stt.call_count == 6
    ids = {c.kwargs["question_id"] for c in fake_stt.call_args_list}
    assert len(ids) == 6, "문항마다 한 번씩 전사해야 합니다"


def test_전사는_세션과_같은_키를_쓴다(client, auth, llm_mode, fake_stt):
    """키가 다르면 캐시가 안 맞아 같은 답변을 두 번 전사한다."""
    make(client, auth, six_answers())

    for call in fake_stt.call_args_list:
        assert call.kwargs["session_id"] == "sess_rp"
        assert call.kwargs["question_id"].startswith("q_")


def test_되묻기_답변도_전사한다(client, auth, llm_mode, fake_stt):
    """되묻기 답변은 원 질문에 이어 붙여 하나로 채점한다."""
    rows = six_answers() + [
        answer(1, type="reask", category=None, difficulty=None, reask_of="q_1")
    ]
    make(client, auth, rows)
    assert fake_stt.call_count == 7


def test_전사가_실패하면_STT_FAILED가_나간다(client, auth, llm_mode, fake_stt):
    fake_stt.side_effect = SttError("무음입니다")
    body = make(client, auth, six_answers())

    assert body["status"] == "error"
    assert body["error_code"] == "STT_FAILED"
    assert "result" not in body


def test_파일을_못_받으면_MEDIA_FETCH_FAILED가_나간다(client, auth, llm_mode, fake_stt):
    """presigned URL 만료가 대부분이다. 백엔드가 새 URL로 한 번 다시 요청하면
    풀리므로, 오디오 자체 문제(STT_FAILED, 재시도 없음)와 구분해야 한다."""
    fake_stt.side_effect = answers.MediaFetchError("HTTP 403")
    body = make(client, auth, six_answers())

    assert body["status"] == "error"
    assert body["error_code"] == "MEDIA_FETCH_FAILED"


def test_URL이_만료되면_MediaFetchError다(monkeypatch):
    import httpx2

    def get(self, url):
        request = httpx2.Request("GET", url)
        raise httpx2.HTTPStatusError(
            "forbidden", request=request, response=httpx2.Response(403, request=request)
        )

    monkeypatch.setattr(httpx2.Client, "get", get)
    with pytest.raises(answers.MediaFetchError):
        answers._download("https://s3.../expired.webm")


def test_리포트도_processing을_거친다(client, auth, llm_mode, fake_stt):
    """실제 서버는 전사와 분석에 시간이 걸린다. 백엔드 폴링 루프가 돌아야 한다."""
    res = client.post(
        "/ai/sessions/sess_rp/report",
        headers={**auth, **key(9)},
        json=report_body(six_answers()),
    )
    body = poll(client, auth, res.json()["task_id"])
    assert body["status"] == "done"
    assert body["result"]["report_status"] == "complete"


def test_더미_모드는_전사하지_않는다(client, auth, fake_stt):
    """백엔드가 지금 검증하고 있는 동작이 바뀌면 안 된다."""
    body = make(client, auth, six_answers())

    assert body["status"] == "done"
    assert fake_stt.call_count == 0


# ---------------------------------------------------------------------------
# 내용 채점 이음매 — D가 함수를 꽂으면 그때부터 진짜 채점이 된다
# ---------------------------------------------------------------------------


@pytest.fixture
def real_scorer():
    """전사 텍스트를 읽고 점수를 내는 채점기를 꽂는다."""
    seen = []

    def scorer(question_text, answer_text):
        seen.append((question_text, answer_text))
        return 12          # 게이트가 걸리는 구간

    report_dummy.CONTENT_SCORER = scorer
    yield seen
    report_dummy.CONTENT_SCORER = None


def test_채점기를_꽂으면_전사_텍스트로_점수가_난다(
    client, auth, llm_mode, fake_stt, real_scorer
):
    body = make(client, auth, six_answers())

    assert body["result"]["axes"]["content"]["score"] == 12
    assert body["result"]["overall"]["gated"] is True
    assert real_scorer, "채점기가 불리지 않았습니다"

    question_text, answer_text = real_scorer[0]
    assert question_text, "질문 텍스트가 넘어가야 관련성을 볼 수 있습니다"
    assert "결제 모듈" in answer_text


def test_채점이_실패하면_CONTENT_FAILED다(client, auth, llm_mode, fake_stt):
    """내용 점수 없이는 게이트를 돌릴 수 없다. 가짜 점수로 채우면
    틀린 총점이 정상 리포트처럼 나간다. (계약서 6장)"""
    def boom(question_text, answer_text):
        raise RuntimeError("채점 실패")

    report_dummy.CONTENT_SCORER = boom
    try:
        body = make(client, auth, six_answers())
    finally:
        report_dummy.CONTENT_SCORER = None

    assert body["status"] == "error"
    assert body["error_code"] == "CONTENT_FAILED"
    assert "result" not in body


def test_말이_없는_답변은_내용_0점이다(client, auth, llm_mode):
    """전사가 비면 답한 내용이 없는 것이다. 가짜 점수를 주면 안 된다."""
    def silent(*, audio_url, session_id, question_id, is_timeout=False):
        return AnswerText(duration_sec=0, word_count=0, text="")

    report_dummy.CONTENT_SCORER = lambda q, a: 90
    try:
        with mock_patch.object(answers, "transcribe", side_effect=silent):
            body = make(client, auth, six_answers())
    finally:
        report_dummy.CONTENT_SCORER = None

    assert body["status"] == "done"
    assert body["result"]["axes"]["content"]["score"] == 0
    assert body["result"]["overall"]["gated"] is True


def test_채점기가_없으면_더미_트리거가_그대로_돈다(client, auth, llm_mode, fake_stt):
    """백엔드가 쓰던 offtopic 트리거가 살아 있어야 한다."""
    body = make(client, auth, six_answers(audio="offtopic"))

    assert body["result"]["overall"]["gated"] is True
    assert body["result"]["axes"]["content"]["score"] < 30


# ---------------------------------------------------------------------------
# 요금 방어 — 내용 채점은 문항마다 실제 API를 부른다
# ---------------------------------------------------------------------------


def test_채점_스위치를_끄면_채점기가_없다(monkeypatch):
    """USE_CONTENT_SCORING=0이면 AI_MODE가 llm이어도 부르지 않는다."""
    monkeypatch.setenv("AI_MODE", "llm")
    monkeypatch.setenv("USE_CONTENT_SCORING", "0")
    monkeypatch.setattr(report_dummy, "CONTENT_SCORER", None)

    assert report_dummy.content_scoring_enabled() is False
    assert report_dummy._scorer() is None


def test_채점_스위치는_호출_시점에_정해진다(monkeypatch):
    """import 시점에 정하면 환경변수를 바꿔도 반영되지 않고,
    테스트가 실제 API를 부를지가 import 순서에 좌우된다."""
    monkeypatch.setattr(report_dummy, "CONTENT_SCORER", None)

    monkeypatch.setenv("USE_CONTENT_SCORING", "0")
    assert report_dummy.content_scoring_enabled() is False

    monkeypatch.setenv("USE_CONTENT_SCORING", "1")
    assert report_dummy.content_scoring_enabled() is True


def test_스위치를_안_정하면_AI_MODE를_따른다(monkeypatch):
    monkeypatch.delenv("USE_CONTENT_SCORING", raising=False)
    monkeypatch.setattr(report_dummy, "CONTENT_SCORER", None)

    monkeypatch.setenv("AI_MODE", "dummy")
    assert report_dummy.content_scoring_enabled() is False

    monkeypatch.setenv("AI_MODE", "llm")
    assert report_dummy.content_scoring_enabled() is True


def test_꽂아둔_채점기가_스위치보다_우선한다(monkeypatch):
    """테스트가 가짜를 꽂으면 환경변수와 무관하게 그것이 쓰여야 한다."""
    def fake(question_text, answer_text):
        return 50

    monkeypatch.setenv("USE_CONTENT_SCORING", "0")
    monkeypatch.setattr(report_dummy, "CONTENT_SCORER", fake)

    assert report_dummy._scorer() is fake
