"""자소서 업로드부터 리포트까지 한 바퀴.

파트별로는 다 되는데 이어 붙였을 때 끊기는 곳이 있으면 여기서 잡힌다.
STT와 채점은 가짜를 꽂는다. GPU도 실제 API도 쓰지 않는다.

각 파트가 붙인 것이 서로 맞물리는지를 본다.

    B의 stt      발화 길이 → 길이 게이트 → 되묻기가 실제로 나오는가
    A의 흐름     꼬리질문이 직전 답변을 근거로 만들어지는가
    D의 채점기    전사 텍스트가 내용 점수로 이어지는가
    리포트       세 축이 합쳐져 총점이 나오는가
"""
from unittest.mock import patch as mock_patch

import time
import pytest

from ai import answers, dummy, report_dummy
from ai.answers import AnswerText

BODY = {
    "resume_file_url": "https://s3.../resume.pdf",
    "job_role": "백엔드 개발",
    "persona": "pressure",
    "question_count": 6,
}

# 짧게 답한 것으로 볼 오디오. 길이 게이트가 되묻기를 발동시킨다.
SHORT = "short"
LONG_TEXT = "결제 모듈을 맡아 재시도 로직을 직접 설계했고 실패율을 3%에서 0.4%로 낮췄습니다."


@pytest.fixture
def stt_on(monkeypatch):
    monkeypatch.setenv("AI_MODE", "dummy")
    monkeypatch.setenv("USE_STT", "1")
    monkeypatch.setenv("USE_CONTENT_SCORING", "0")


@pytest.fixture
def fake_stt():
    """B의 stt.py가 돌려주는 모양 그대로를 흉내낸다."""
    def transcribe(*, audio_url, session_id, question_id, is_timeout=False):
        short = SHORT in audio_url
        return AnswerText(
            duration_sec=5 if short else 45,
            word_count=10 if short else 62,
            text="네." if short else LONG_TEXT,
        )

    with mock_patch.object(answers, "transcribe", side_effect=transcribe) as m:
        yield m


@pytest.fixture
def fake_scorer():
    """D의 채점기 자리. 짧은 답변은 낮게, 충실한 답변은 높게."""
    def scorer(question_text, answer_text):
        return 20 if answer_text == "네." else 78

    report_dummy.CONTENT_SCORER = scorer
    yield scorer
    report_dummy.CONTENT_SCORER = None


# 백그라운드 작업이 끝나길 기다리는 시간. 횟수로 세면 컴퓨터가 바쁠 때
# 작업이 끝나기 전에 포기해서 테스트가 가끔 실패한다. 시간으로 센다.
POLL_TIMEOUT_SEC = 10.0
POLL_INTERVAL_SEC = 0.01


def _poll(client, auth, task_id):
    deadline = time.monotonic() + POLL_TIMEOUT_SEC
    while True:
        res = client.get(f"/ai/tasks/{task_id}", headers=auth)
        assert res.status_code == 200, res.json()
        body = res.json()
        if body["status"] in ("done", "error"):
            return body
        if time.monotonic() > deadline:
            raise AssertionError("작업이 끝나지 않았습니다")
        time.sleep(POLL_INTERVAL_SEC)


def _run_session(client, auth):
    """면접을 끝까지 진행하고 리포트 요청에 쓸 answers[]를 만든다."""
    res = client.post("/ai/sessions", headers=auth, json=BODY)
    assert res.status_code == 202, res.json()
    session_id = res.json()["session_id"]
    item = _poll(client, auth, res.json()["task_id"])["result"]

    rows = []
    for turn in range(1, 26):
        if item["type"] == "session_end":
            break
        # 세 번째 답변마다 짧게 답해 되묻기를 유도한다
        audio = f"https://s3.../a{turn}{'_' + SHORT if turn % 3 == 0 else ''}.webm"
        rows.append({
            "question_id": item["question_id"],
            "type": item["type"],
            "text": item["text"],
            "category": item["category"],
            "difficulty": item["difficulty"],
            "question_number": item["question_number"],
            "audio_url": audio,
            "video_url": f"https://s3.../v{turn}.mp4",
            "is_timeout": False,
            "reask_of": item["reask_of"],
            "is_replay": False,
            "is_spare_topic": item["is_spare_topic"],
        })
        answer = client.post(f"/ai/sessions/{session_id}/answers", headers=auth, json={
            "question_id": item["question_id"],
            "audio_url": audio,
            "video_url": None,
            "is_timeout": False,
        })
        assert answer.status_code == 202, answer.json()
        item = _poll(client, auth, answer.json()["task_id"])["result"]

    assert item["type"] == "session_end", "세션이 끝나지 않았습니다"
    return session_id, rows


# ---------------------------------------------------------------------------


def test_면접_한_바퀴가_돈다(client, auth, stt_on, fake_stt, fake_scorer):
    session_id, rows = _run_session(client, auth)

    kinds = {}
    for row in rows:
        kinds[row["type"]] = kinds.get(row["type"], 0) + 1

    # 세 종류가 다 나와야 흐름이 살아 있는 것이다
    assert kinds.get("question", 0) >= 2, kinds
    assert kinds.get("reask", 0) >= 1, "짧은 답변에 되묻기가 안 나왔습니다"

    # 전사를 답변마다 한 번씩 탔다
    assert fake_stt.call_count == len(rows)

    report = client.post(
        f"/ai/sessions/{session_id}/report",
        headers={**auth, "Idempotency-Key": "rpt_e2e_01"},
        json={
            "persona": "pressure",
            "job_role": "백엔드 개발",
            "company_profile_override": "네이버 (IT/플랫폼)\n\n핵심 가치\n  도전 — 새로 시도한다",
            "answers": rows,
        },
    )
    assert report.status_code == 202, report.json()
    body = _poll(client, auth, report.json()["task_id"])

    assert body["status"] == "done", body
    result = body["result"]
    assert result["report_status"] == "complete"
    assert 0 <= result["overall"]["score"] <= 100
    assert result["axes"]["content"]["status"] == "ok"
    # 인재상을 보냈으니 코멘트가 나와야 한다
    assert result["company_comment"]


def test_전사한_텍스트가_내용_점수로_이어진다(client, auth, stt_on, fake_stt, fake_scorer):
    """채점기가 전사 텍스트를 실제로 받는지 본다."""
    seen = []

    def watching(question_text, answer_text):
        seen.append(answer_text)
        return 78

    report_dummy.CONTENT_SCORER = watching
    try:
        session_id, rows = _run_session(client, auth)
        report = client.post(
            f"/ai/sessions/{session_id}/report",
            headers={**auth, "Idempotency-Key": "rpt_e2e_02"},
            json={"persona": "pressure", "job_role": "백엔드 개발", "answers": rows},
        )
        body = _poll(client, auth, report.json()["task_id"])
    finally:
        report_dummy.CONTENT_SCORER = None

    assert body["status"] == "done"
    assert seen, "채점기가 불리지 않았습니다"
    assert LONG_TEXT in seen, "전사 텍스트가 채점기까지 오지 않았습니다"
    assert body["result"]["axes"]["content"]["score"] == 78


def test_인재상을_안_보내면_코멘트가_null이다(client, auth, stt_on, fake_stt, fake_scorer):
    """회사 미선택 연습 모드. 에러가 아니라 정상 경로다."""
    session_id, rows = _run_session(client, auth)
    report = client.post(
        f"/ai/sessions/{session_id}/report",
        headers={**auth, "Idempotency-Key": "rpt_e2e_03"},
        json={"persona": "pressure", "job_role": "백엔드 개발", "answers": rows},
    )
    body = _poll(client, auth, report.json()["task_id"])

    assert body["status"] == "done"
    assert body["result"]["company_comment"] is None
