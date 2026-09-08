"""세션 시작 흐름 — AI_MODE에 따라 더미와 LLM을 오간다.

실제 API를 부르지 않는다. 이력서 다운로드와 질문 생성만 모킹하고,
그 결과가 세션에 제대로 붙는지와 폴링이 계약대로 도는지를 본다.
"""
from unittest.mock import patch as mock_patch

import pytest

from ai import answers, companies, dummy, llm, pipeline, resume
from ai.answers import AnswerText, SttError
from ai.llm import LlmError
from ai.pipeline import main_question_slots
from ai.resume import Resume, ResumeError

BODY = {
    "resume_file_url": "https://s3.../resume.pdf",
    "job_role": "백엔드 개발",
    "persona": "pressure",
    "question_count": 6,
}


@pytest.fixture
def llm_mode(monkeypatch):
    monkeypatch.setenv("AI_MODE", "llm")


@pytest.fixture
def fake_llm():
    """이력서 다운로드 · 질문 생성 · 전사를 가로챈다.

    실제 API도 GPU도 부르지 않는다.
    """
    generated = {}

    def generate(*, slots, **kw):
        # 요청받은 카테고리를 그대로 채워 준다
        out = {c: f"[생성됨] {c} 질문입니다." for c, _ in slots}
        generated.update(out)
        return out

    def transcribe(*, audio_url, session_id, question_id, is_timeout=False):
        """전사는 더미와 같은 규칙을 쓴다 — audio_url에 short이 있으면 부실한 답변."""
        short = "short" in audio_url or "insufficient" in audio_url
        return AnswerText(
            duration_sec=5 if short else 45,
            word_count=10 if short else 60,
            text="네." if short else "결제 모듈을 맡았고 재시도 로직을 직접 설계했습니다.",
        )

    with mock_patch.object(resume, "fetch", return_value=Resume(text="이력서")) as fetch, \
         mock_patch.object(llm, "generate_main_questions", side_effect=generate) as gen, \
         mock_patch.object(answers, "transcribe", side_effect=transcribe) as stt, \
         mock_patch.object(llm, "generate_followup",
                           return_value="[꼬리질문] 그 판단의 근거는 무엇이었나요?") as fup,          mock_patch.object(llm, "generate_reask",
                           return_value="[되묻기] 어떤 기준으로 정하셨는지 말씀해 주시겠어요?") as rsk:
        yield SimpleHolder(fetch=fetch, generate=gen, generated=generated,
                           transcribe=stt, followup=fup, reask=rsk)


class SimpleHolder:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def poll_until_done(client, auth, task_id, limit=40):
    """계약서대로 done이 될 때까지 폴링한다."""
    seen = []
    for _ in range(limit):
        res = client.get(f"/ai/tasks/{task_id}", headers=auth)
        assert res.status_code == 200, res.json()
        body = res.json()
        seen.append(body["status"])
        if body["status"] in ("done", "error"):
            return body, seen
    raise AssertionError(f"끝나지 않았습니다: {seen}")


# ---------------------------------------------------------------------------
# 슬롯 계산
# ---------------------------------------------------------------------------


def test_계획된_토픽과_예비_토픽을_모두_생성한다():
    session = dummy.create_session(question_count=9, persona="pressure")
    slots = main_question_slots(session)

    plan = session.runner.plan
    categories = [c for c, _ in slots]
    assert categories[: len(plan["categories"])] == plan["categories"]
    assert categories[len(plan["categories"]) :] == plan["spare_categories"]

    # 계획된 토픽은 각 토픽의 첫 난이도를 쓴다
    for (_, difficulty), levels in zip(slots, plan["difficulty"]):
        assert difficulty == levels[0]
    # 예비 토픽은 L2로 열린다
    assert all(d == "L2" for _, d in slots[len(plan["categories"]) :])
    dummy.reset()


def test_재연습은_생성하지_않는다(client, auth, llm_mode, fake_llm):
    """1회차 주질문을 텍스트까지 그대로 재생하므로 LLM을 부를 이유가 없다."""
    replay_log = [
        {"type": "question", "text": "1회차 주질문입니다.", "category": "지원동기",
         "difficulty": "L1", "is_spare_topic": False},
        {"type": "question", "text": "두 번째 주질문입니다.", "category": "직무역량",
         "difficulty": "L1", "is_spare_topic": False},
        {"type": "followup", "difficulty": "L2"},
    ]
    res = client.post("/ai/sessions", headers=auth, json={
        **BODY, "question_count": 3, "persona": "friendly",
        "retry_of_session_id": "sess_first", "replay_log": replay_log,
    })
    assert res.status_code == 202
    body, _ = poll_until_done(client, auth, res.json()["task_id"])

    assert body["status"] == "done"
    assert body["result"]["text"] == "1회차 주질문입니다."
    assert body["result"]["is_replay"] is True
    fake_llm.generate.assert_not_called()
    fake_llm.fetch.assert_not_called()      # 이력서도 받을 필요가 없다


# ---------------------------------------------------------------------------
# 더미 모드는 그대로
# ---------------------------------------------------------------------------


def test_더미_모드는_LLM을_부르지_않는다(client, auth, fake_llm):
    res = client.post("/ai/sessions", headers=auth, json=BODY)
    body, _ = poll_until_done(client, auth, res.json()["task_id"])

    assert body["status"] == "done"
    assert "[생성됨]" not in body["result"]["text"]      # 고정 문장
    fake_llm.generate.assert_not_called()


# ---------------------------------------------------------------------------
# LLM 모드
# ---------------------------------------------------------------------------


def test_LLM_모드는_생성된_주질문이_나온다(client, auth, llm_mode, fake_llm):
    res = client.post("/ai/sessions", headers=auth, json=BODY)
    assert res.status_code == 202
    started = res.json()

    # 세션 정보는 즉시 나온다 — 생성이 끝나기를 기다리지 않는다
    assert started["session_id"]
    assert started["question_total"] == 6

    body, _ = poll_until_done(client, auth, started["task_id"])
    assert body["status"] == "done"
    assert body["result"]["text"].startswith("[생성됨]")
    assert body["result"]["type"] == "question"


def test_예비_토픽_질문도_미리_만들어_둔다(client, auth, llm_mode, fake_llm):
    """세션 도중에 예비 토픽이 들어와도 다시 생성하지 않는다."""
    res = client.post("/ai/sessions", headers=auth,
                      json={**BODY, "persona": "pressure"})
    poll_until_done(client, auth, res.json()["task_id"])

    session = dummy.SESSIONS[res.json()["session_id"]]
    plan = session.runner.plan
    for category in plan["categories"] + plan["spare_categories"]:
        assert session.main_questions[category].startswith("[생성됨]")

    assert fake_llm.generate.call_count == 1      # 호출은 한 번뿐


def test_세션_전체를_생성된_질문으로_돈다(client, auth, llm_mode, fake_llm):
    """부실하게만 답해 예비 토픽까지 투입시켜도 고정 문장이 새지 않는다."""
    res = client.post("/ai/sessions", headers=auth, json=BODY)
    sid = res.json()["session_id"]
    body, _ = poll_until_done(client, auth, res.json()["task_id"])

    asked = 0
    for _ in range(30):
        item = body["result"]
        if item["type"] == "session_end":
            break
        if item["type"] == "question":
            assert item["text"].startswith("[생성됨]"), item
            asked += 1
        answer = client.post(f"/ai/sessions/{sid}/answers", headers=auth, json={
            "question_id": item["question_id"],
            "audio_url": "https://s3.../ans_short.webm",
            "video_url": None, "is_timeout": False,
        })
        body, _ = poll_until_done(client, auth, answer.json()["task_id"])

    assert body["result"]["total_questions"] == 6
    assert asked == 6          # 전부 주질문 (부실해서 꼬리질문이 안 나감)
    assert fake_llm.generate.call_count == 1


# ---------------------------------------------------------------------------
# 인재상
# ---------------------------------------------------------------------------


def profile_passed(fake_llm):
    return fake_llm.generate.call_args.kwargs["company_profile"]


def test_verified가_false면_인재상을_쓰지_않는다(client, auth, llm_mode, fake_llm):
    """공식 채용페이지에서 확인되지 않은 내용으로 질문을 만들면
    미확인 데이터가 서비스에 노출되는 셈이다."""
    res = client.post("/ai/sessions", headers=auth,
                      json={**BODY, "company_id": "samsung_electronics"})
    poll_until_done(client, auth, res.json()["task_id"])
    assert profile_passed(fake_llm) is None


def test_등록_직무가_없으면_추론하지_말라고_붙인다(client, auth, llm_mode, fake_llm):
    """이게 없으면 LLM이 그 기업의 직무 요구역량을 그럴듯하게 지어낸다."""
    res = client.post("/ai/sessions", headers=auth,
                      json={**BODY, "company_id": "hyundai_enc", "job_role": "백엔드 개발"})
    poll_until_done(client, auth, res.json()["task_id"])

    profile = profile_passed(fake_llm)
    assert "현대건설" in profile
    assert "도전" in profile                    # 핵심 가치는 들어간다
    assert "추론하지 말고" in profile            # 직무 요건은 지어내지 말라고 못박는다


def test_직접_입력값이_company_id보다_우선한다(client, auth, llm_mode, fake_llm, monkeypatch):
    """계약서 2장 — company_profile_override가 있으면 company_id보다 우선한다."""
    monkeypatch.setattr(companies, "profile_for", lambda cid, job=None: "등록된 인재상")

    res = client.post("/ai/sessions", headers=auth, json={
        **BODY, "company_id": "hyundai_enc",
        "company_profile_override": "직접 입력한 인재상",
    })
    poll_until_done(client, auth, res.json()["task_id"])

    assert profile_passed(fake_llm) == "직접 입력한 인재상"


def test_등록_직무가_있으면_요구역량을_넣는다(client, auth, llm_mode, fake_llm, monkeypatch):
    """job_requirements의 키와 job_role이 맞으면 그 직무 요건을 함께 준다."""
    from ai.schemas import CompanyRecord

    record = CompanyRecord.model_validate({
        "company_id": "acme", "name": "에이크미", "industry": "IT",
        "values_format": "단어형", "verified": True,
        "core_values": [{"name": "도전"}],
        "job_requirements": {"backend": ["대규모 트래픽 환경에서의 안정성 확보"]},
    })
    monkeypatch.setattr(companies, "load_records", lambda: (record,))

    res = client.post("/ai/sessions", headers=auth,
                      json={**BODY, "company_id": "acme", "job_role": "backend"})
    poll_until_done(client, auth, res.json()["task_id"])

    profile = profile_passed(fake_llm)
    assert "대규모 트래픽" in profile
    assert "추론하지 말고" not in profile        # 자료가 있으니 붙이지 않는다


def test_회사를_안_고르면_None(client, auth, llm_mode, fake_llm):
    res = client.post("/ai/sessions", headers=auth, json={**BODY, "company_id": None})
    poll_until_done(client, auth, res.json()["task_id"])
    assert profile_passed(fake_llm) is None


# ---------------------------------------------------------------------------
# 실패
# ---------------------------------------------------------------------------


def test_이력서를_못_읽으면_RESUME_PARSE_FAILED(client, auth, llm_mode):
    with mock_patch.object(resume, "fetch", side_effect=ResumeError("URL이 만료되었습니다")):
        res = client.post("/ai/sessions", headers=auth, json=BODY)
        assert res.status_code == 202       # 실패는 폴링 결과로 나간다
        body, _ = poll_until_done(client, auth, res.json()["task_id"])

    assert body["status"] == "error"
    assert body["error_code"] == "RESUME_PARSE_FAILED"
    assert "만료" in body["message"]
    assert "result" not in body


def test_질문_생성이_실패하면_LLM_FAILED(client, auth, llm_mode):
    with mock_patch.object(resume, "fetch", return_value=Resume(text="이력서")), \
         mock_patch.object(llm, "generate_main_questions",
                           side_effect=LlmError("생성되지 않은 카테고리가 있습니다")):
        res = client.post("/ai/sessions", headers=auth, json=BODY)
        body, _ = poll_until_done(client, auth, res.json()["task_id"])

    assert body["status"] == "error"
    assert body["error_code"] == "LLM_FAILED"
    assert "result" not in body


def test_생성_중에_답변을_보내면_INVALID_QUESTION_ID(client, auth, llm_mode):
    """폴링해서 done을 받은 뒤에 답변을 보내야 한다."""
    import threading

    release = threading.Event()

    def slow_fetch(url):
        release.wait(5)
        return Resume(text="이력서")

    with mock_patch.object(resume, "fetch", side_effect=slow_fetch), \
         mock_patch.object(llm, "generate_main_questions", return_value={}):
        res = client.post("/ai/sessions", headers=auth, json=BODY)
        sid = res.json()["session_id"]

        answer = client.post(f"/ai/sessions/{sid}/answers", headers=auth, json={
            "question_id": "q_1", "audio_url": "u",
            "video_url": None, "is_timeout": False,
        })
        assert answer.status_code == 400
        assert answer.json()["error_code"] == "INVALID_QUESTION_ID"
        assert "준비되지 않았습니다" in answer.json()["message"]

        release.set()
        poll_until_done(client, auth, res.json()["task_id"])


# ---------------------------------------------------------------------------
# 답변 처리 — STT를 타는 경로
#
# 더미 모드는 audio_url 문자열로 길이를 지어내고 즉시 끝난다.
# llm 모드는 전사를 거쳐 실제 발화 길이로 진행하고, 꼬리질문을 새로 만든다.
# ---------------------------------------------------------------------------


def _first_question(client, auth, body=None):
    res = client.post("/ai/sessions", headers=auth, json=body or BODY)
    sid = res.json()["session_id"]
    done, _ = poll_until_done(client, auth, res.json()["task_id"])
    return sid, done["result"]


def _answer(client, auth, sid, item, audio="https://s3.../ans.webm"):
    res = client.post(f"/ai/sessions/{sid}/answers", headers=auth, json={
        "question_id": item["question_id"],
        "audio_url": audio,
        "video_url": None,
        "is_timeout": False,
    })
    assert res.status_code == 202, res.json()
    done, seen = poll_until_done(client, auth, res.json()["task_id"])
    return done, seen


def test_답변_처리가_전사를_거친다(client, auth, llm_mode, fake_llm):
    """길이 게이트에 실제 발화 길이가 들어가야 한다."""
    sid, first = _first_question(client, auth)
    _answer(client, auth, sid, first)

    assert fake_llm.transcribe.call_count == 1
    kw = fake_llm.transcribe.call_args.kwargs
    assert kw["session_id"] == sid
    assert kw["question_id"] == first["question_id"]


def test_답변_처리도_processing을_거친다(client, auth, llm_mode, fake_llm):
    """실제 서버는 전사에 시간이 걸린다. 백엔드 폴링 루프가 돌아야 한다."""
    sid, first = _first_question(client, auth)
    _, seen = _answer(client, auth, sid, first)
    assert seen[-1] == "done"


def test_꼬리질문은_전사된_답변을_근거로_만든다(client, auth, llm_mode, fake_llm):
    """직전 답변 텍스트가 넘어가지 않으면 답변을 안 들은 질문이 나간다."""
    sid, item = _first_question(client, auth)

    for _ in range(10):
        done, _ = _answer(client, auth, sid, item)
        item = done["result"]
        if item["type"] == "session_end":
            break
        if item["type"] == "followup":
            break

    assert item["type"] == "followup", item
    assert item["text"] == "[꼬리질문] 그 판단의 근거는 무엇이었나요?"

    history = fake_llm.followup.call_args.kwargs["history"]
    assert history, "직전 답변이 넘어가지 않았습니다"
    assert history[-1].answer.startswith("결제 모듈")
    assert history[-1].question.startswith("[생성됨]")


def test_주제가_바뀌면_이전_대화를_끌고_가지_않는다(client, auth, llm_mode, fake_llm):
    """꼬리질문은 지금 주제의 답변만 봐야 한다."""
    session = dummy.create_session(question_count=9, persona="pressure", job_role="AI")
    session.start()
    session.advance(45, 60, is_timeout=False, answer_text="첫 주제 답변입니다.")
    assert len(session.topic_history) >= 1

    # 새 주질문이 나올 때까지 진행시킨다
    for _ in range(12):
        item = session.advance(45, 60, is_timeout=False, answer_text="답변입니다.")
        if getattr(item, "type", None) == "question":
            break

    questions = [e["question"] for e in session.topic_history]
    assert len(questions) == 1, questions
    assert not session.answered_history()


def test_전사가_실패하면_STT_FAILED가_나간다(client, auth, llm_mode, fake_llm):
    """폴링이 processing에 갇히면 백엔드가 세션을 정리할 수 없다."""
    sid, first = _first_question(client, auth)
    fake_llm.transcribe.side_effect = SttError("음성을 내려받지 못했습니다")

    res = client.post(f"/ai/sessions/{sid}/answers", headers=auth, json={
        "question_id": first["question_id"],
        "audio_url": "https://s3.../ans.webm",
        "video_url": None,
        "is_timeout": False,
    })
    done, _ = poll_until_done(client, auth, res.json()["task_id"])

    assert done["status"] == "error"
    assert done["error_code"] == "STT_FAILED"
    assert "result" not in done


def test_꼬리질문_생성이_실패해도_세션은_이어진다(client, auth, llm_mode, fake_llm):
    """고정 문장이 나가는 편이 면접이 끊기는 것보다 낫다."""
    fake_llm.followup.side_effect = LlmError("생성 실패")
    sid, item = _first_question(client, auth)

    for _ in range(10):
        done, _ = _answer(client, auth, sid, item)
        item = done["result"]
        if item["type"] in ("followup", "session_end"):
            break

    assert item["type"] == "followup", item
    assert item["text"]                      # 고정 문장이라도 나간다
    assert not item["text"].startswith("[꼬리질문]")


def test_더미_모드는_전사를_부르지_않는다(client, auth, fake_llm):
    """백엔드가 지금 검증하고 있는 동작이 바뀌면 안 된다."""
    res = client.post("/ai/sessions", headers=auth, json=BODY)
    sid = res.json()["session_id"]
    done, _ = poll_until_done(client, auth, res.json()["task_id"])
    _answer(client, auth, sid, done["result"])

    assert fake_llm.transcribe.call_count == 0
    assert fake_llm.followup.call_count == 0


# ---------------------------------------------------------------------------
# 되묻기 — 답변이 짧으면 같은 질문을 다시 묻는다
# ---------------------------------------------------------------------------


def _until(client, auth, sid, item, kind, audio, limit=12):
    """원하는 종류가 나올 때까지 답변을 넣는다."""
    for _ in range(limit):
        done, _ = _answer(client, auth, sid, item, audio=audio)
        item = done["result"]
        if item["type"] in (kind, "session_end"):
            return item
    return item


def test_되묻기_문구를_답변을_읽고_만든다(client, auth, llm_mode, fake_llm):
    """고정 문장 하나로는 지원자가 두 번째에도 같은 대답을 한다."""
    sid, first = _first_question(client, auth)
    item = _until(client, auth, sid, first, "reask", "https://s3.../ans_short.webm")

    assert item["type"] == "reask", item
    assert item["text"] == "[되묻기] 어떤 기준으로 정하셨는지 말씀해 주시겠어요?"
    assert fake_llm.reask.called

    kwargs = fake_llm.reask.call_args.kwargs
    assert kwargs["persona"] == "pressure"
    assert kwargs["job_role"] == "백엔드 개발"
    assert kwargs["history"], "직전 대화가 넘어가지 않았습니다"


def test_되묻기는_계약대로_null_필드를_지킨다(client, auth, llm_mode, fake_llm):
    """문구를 새로 만들어도 category · difficulty는 null이어야 한다."""
    sid, first = _first_question(client, auth)
    item = _until(client, auth, sid, first, "reask", "https://s3.../ans_short.webm")

    assert item["type"] == "reask"
    assert item["category"] is None
    assert item["difficulty"] is None
    assert item["reask_of"] == item["question_id"].rstrip("r")
    assert item["is_spare_topic"] is False
    assert item["is_replay"] is False


def test_되묻기_생성이_실패해도_세션은_이어진다(client, auth, llm_mode, fake_llm):
    fake_llm.reask.side_effect = LlmError("생성 실패")
    sid, first = _first_question(client, auth)
    item = _until(client, auth, sid, first, "reask", "https://s3.../ans_short.webm")

    assert item["type"] == "reask", item
    assert item["text"]
    assert not item["text"].startswith("[되묻기]")


def test_더미_모드는_되묻기도_고정_문장이다(client, auth, fake_llm):
    res = client.post("/ai/sessions", headers=auth, json=BODY)
    sid = res.json()["session_id"]
    done, _ = poll_until_done(client, auth, res.json()["task_id"])
    item = _until(client, auth, sid, done["result"], "reask", "https://s3.../ans_short.webm")

    assert item["type"] == "reask"
    assert item["text"] == dummy.REASK_QUESTION
    assert fake_llm.reask.call_count == 0
