"""비동기 폴링 흉내 — DUMMY_POLL_TICKS.

실제 서버는 STT + LLM + TTS가 순차로 돌아 폴링하면 processing을 여러 번 거친 뒤
done이 된다. 더미는 즉시 계산해두기 때문에 그대로 두면 백엔드의 processing 분기가
한 번도 실행되지 않고, 2주차에 실제 모델이 붙는 순간 처음 겪게 된다.

이 스위치를 켜면 백엔드가 폴링 루프를 지금 검증할 수 있다.
"""
import pytest

from ai import dummy


@pytest.fixture
def ticks(monkeypatch):
    """DUMMY_POLL_TICKS를 세팅한다. 태스크는 저장 시점의 값으로 고정된다."""
    def _set(n):
        monkeypatch.setenv(dummy.POLL_TICKS_ENV, str(n))
    return _set


def start(client, auth):
    res = client.post(
        "/ai/sessions", headers=auth,
        json={"resume_file_url": "u", "job_role": "백엔드 개발", "persona": "pressure"},
    )
    assert res.status_code == 202
    return res.json()


def poll(client, auth, task_id):
    res = client.get(f"/ai/tasks/{task_id}", headers=auth)
    assert res.status_code == 200, res.json()
    return res.json()


# ---------------------------------------------------------------------------
# 기본값 — 기존 동작
# ---------------------------------------------------------------------------


def test_기본값이면_즉시_done(client, auth):
    """DUMMY_POLL_TICKS를 설정하지 않으면 예전처럼 바로 결과가 나온다."""
    assert dummy.poll_ticks() == 0
    body = poll(client, auth, start(client, auth)["task_id"])
    assert body["status"] == "done"


@pytest.mark.parametrize("bad", ["", "abc", "-3"])
def test_잘못된_값이면_0으로_본다(monkeypatch, bad):
    monkeypatch.setenv(dummy.POLL_TICKS_ENV, bad)
    assert dummy.poll_ticks() == 0


# ---------------------------------------------------------------------------
# 켰을 때 — 질문 생성
# ---------------------------------------------------------------------------


def test_지정한_횟수만큼_processing이_나온다(client, auth, ticks):
    ticks(3)
    task_id = start(client, auth)["task_id"]

    seen = []
    for _ in range(3):
        body = poll(client, auth, task_id)
        assert body["status"] == "processing", body
        assert "result" not in body          # 아직 결과가 없다
        seen.append(body["stage"])

    # 네 번째 폴링에서 완료된다
    final = poll(client, auth, task_id)
    assert final["status"] == "done"
    assert final["result"]["type"] == "question"

    # 이후로도 계속 done이다 (폴링을 더 해도 안전하다)
    assert poll(client, auth, task_id)["status"] == "done"

    assert seen == ["stt", "generating", "tts"]


def test_stage는_계약서_4장의_3종만_나온다(client, auth, ticks):
    ticks(9)
    task_id = start(client, auth)["task_id"]
    stages = {poll(client, auth, task_id)["stage"] for _ in range(9)}
    assert stages <= {"stt", "generating", "tts"}
    assert poll(client, auth, task_id)["status"] == "done"


def test_질문_폴링에는_progress가_없다(client, auth, ticks):
    """progress는 리포트 폴링에만 있다. (계약서 4장 vs 리포트 계약 3장)"""
    ticks(2)
    body = poll(client, auth, start(client, auth)["task_id"])
    assert body["status"] == "processing"
    assert set(body) == {"status", "stage"}


def test_ticks가_1이면_한_번만_processing(client, auth, ticks):
    ticks(1)
    task_id = start(client, auth)["task_id"]
    assert poll(client, auth, task_id)["status"] == "processing"
    assert poll(client, auth, task_id)["status"] == "done"


def test_답변_제출_태스크도_processing을_거친다(client, auth, ticks):
    ticks(2)
    started = start(client, auth)
    sid = started["session_id"]

    # 첫 질문을 끝까지 폴링한다
    task_id = started["task_id"]
    while poll(client, auth, task_id)["status"] != "done":
        pass
    qid = poll(client, auth, task_id)["result"]["question_id"]

    res = client.post(
        f"/ai/sessions/{sid}/answers", headers=auth,
        json={"question_id": qid, "audio_url": "https://s3.../a.webm",
              "video_url": None, "is_timeout": False},
    )
    assert res.status_code == 202
    answer_task = res.json()["task_id"]

    assert poll(client, auth, answer_task)["status"] == "processing"
    assert poll(client, auth, answer_task)["status"] == "processing"
    assert poll(client, auth, answer_task)["status"] == "done"


def test_세션을_끝까지_돌릴_수_있다(client, auth, ticks):
    """폴링 루프를 실제로 돌려도 문항 수가 그대로 지켜지는가."""
    ticks(2)
    started = start(client, auth)
    sid, task_id = started["session_id"], started["task_id"]

    def wait(tid):
        for _ in range(20):
            body = poll(client, auth, tid)
            if body["status"] == "done":
                return body["result"]
        raise AssertionError("done이 되지 않았다")

    item = wait(task_id)
    asked = 0
    for _ in range(30):
        if item["type"] == "session_end":
            break
        if item["type"] != "reask":
            asked += 1
        res = client.post(
            f"/ai/sessions/{sid}/answers", headers=auth,
            json={"question_id": item["question_id"], "audio_url": "https://s3.../a.webm",
                  "video_url": None, "is_timeout": False},
        )
        item = wait(res.json()["task_id"])

    assert item["type"] == "session_end"
    assert item["total_questions"] == 6
    assert asked == 6


# ---------------------------------------------------------------------------
# 켰을 때 — 리포트
# ---------------------------------------------------------------------------


def report_body(n=3):
    return {
        "persona": "pressure",
        "job_role": "백엔드 개발",
        "company_id": None,
        "company_profile_override": None,
        "answers": [
            {
                "question_id": f"q_{i}", "type": "question", "text": f"{i}번 질문",
                "category": "지원동기", "difficulty": "L1", "question_number": i,
                "audio_url": f"https://s3.../ans_{i}.webm", "video_url": None,
                "is_timeout": False, "reask_of": None,
                "is_replay": True, "is_spare_topic": False,
            }
            for i in range(1, n + 1)
        ],
    }


def test_리포트_폴링은_stage가_다르고_progress가_있다(client, auth, ticks):
    """리포트 계약 3장 — transcribing … composing, progress 포함."""
    ticks(5)
    res = client.post(
        "/ai/sessions/sess_p/report",
        headers={**auth, "Idempotency-Key": "rpt_p_01"},
        json=report_body(),
    )
    assert res.status_code == 202
    task_id = res.json()["task_id"]

    seen = []
    for _ in range(5):
        body = poll(client, auth, task_id)
        assert body["status"] == "processing", body
        assert 0.0 < body["progress"] < 1.0
        seen.append(body["stage"])

    assert seen == [
        "transcribing", "analyzing_speech", "analyzing_gaze",
        "analyzing_content", "composing",
    ]
    final = poll(client, auth, task_id)
    assert final["status"] == "done"
    assert final["result"]["report_status"] == "complete"


def test_progress는_단조증가한다(client, auth, ticks):
    ticks(4)
    res = client.post(
        "/ai/sessions/sess_p/report",
        headers={**auth, "Idempotency-Key": "rpt_p_02"},
        json=report_body(),
    )
    task_id = res.json()["task_id"]
    values = [poll(client, auth, task_id)["progress"] for _ in range(4)]
    assert values == sorted(values)
    assert len(set(values)) > 1


def test_content_실패도_processing을_거친_뒤_error(client, auth, ticks):
    """분석이 돌다가 실패하는 것이므로 즉시 error가 아니다."""
    ticks(2)
    body = report_body()
    for a in body["answers"]:
        a["audio_url"] = a["audio_url"].replace(".webm", "_content_fail.webm")

    res = client.post(
        "/ai/sessions/sess_cf/report",
        headers={**auth, "Idempotency-Key": "rpt_cf_01"},
        json=body,
    )
    task_id = res.json()["task_id"]

    assert poll(client, auth, task_id)["status"] == "processing"
    assert poll(client, auth, task_id)["status"] == "processing"

    final = poll(client, auth, task_id)
    assert final["status"] == "error"
    assert final["error_code"] == "CONTENT_FAILED"
    assert "result" not in final


# ---------------------------------------------------------------------------
# 잘못 짠 백엔드 코드가 여기서 걸린다
# ---------------------------------------------------------------------------


def test_processing_응답에는_result가_없다(client, auth, ticks):
    """더미만 보고 task.result를 바로 꺼내 쓰면 2주차에 NPE가 난다.

    이 스위치를 켜고 통합 테스트를 돌리면 그 코드가 지금 걸린다.
    """
    ticks(2)
    body = poll(client, auth, start(client, auth)["task_id"])
    assert body["status"] == "processing"
    assert body.get("result") is None
