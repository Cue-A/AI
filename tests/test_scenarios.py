"""docs/질문 유형.md 13장의 진행 시나리오 재현.

시나리오 A   9문항 압박형, 전부 충분한 답변   → 9문항, 되묻기 0회, 토픽 4개
시나리오 B   6문항 친절형, 중간에 부실 1회    → 6문항, 되묻기 1회, 토픽 3개
시나리오 C   6문항 압박형, 전부 부실          → 6문항, 되묻기 3회, 토픽 6개
시나리오 D   6문항 친절형, 매우 충실한 답변    → 난이도 상향

A · B · C · 재연습은 FastAPI TestClient로 실제 HTTP 요청을 보낸다.
함수를 직접 호출하지 않는다.

D만 예외로 SessionRunner를 직접 호출한다. 난이도 상향은 verdict="escalate"일 때만
일어나는데, 계약서의 답변 제출 요청에는 verdict를 넣을 방법이 없기 때문이다.
서버 런타임은 계속 verdict=None을 넘긴다. 2단계 LLM 판정을 켤 때 이 경로가 이미
검증되어 있어야 하므로 테스트로만 확인한다.

배분 패턴은 세션마다 무작위로 정해진다. 패턴을 고정하지 않고 결과값만 검증한다.
어떤 패턴이 걸리든 문항 수와 되묻기 횟수는 같아야 정상이기 때문이다.
"""
import pytest

from ai.session_plan import SessionRunner, build_plan

# 발화 길이는 audio_url에서 정해진다. ai/dummy.answer_length 참고.
OK_AUDIO = "https://s3.../ans_{n}.webm"
SHORT_AUDIO = "https://s3.../ans_{n}_short.webm"

MAX_TURNS = 60


# ---------------------------------------------------------------------------
# HTTP 헬퍼 — 전부 실제 요청을 보낸다
# ---------------------------------------------------------------------------


def start_session(client, auth, *, question_count, persona, replay_log=None):
    body = {
        "resume_file_url": "https://s3.../resume_abc.pdf",
        "job_role": "백엔드 개발",
        "persona": persona,
        "question_count": question_count,
    }
    if replay_log is not None:
        body["replay_log"] = replay_log
    res = client.post("/ai/sessions", headers=auth, json=body)
    assert res.status_code == 202, res.json()
    return res.json()


def poll(client, auth, task_id):
    res = client.get(f"/ai/tasks/{task_id}", headers=auth)
    assert res.status_code == 200, res.json()
    body = res.json()
    assert body["status"] == "done", body
    return body["result"]


def submit_answer(client, auth, session_id, question_id, audio_url, *, is_timeout=False):
    res = client.post(
        f"/ai/sessions/{session_id}/answers",
        headers=auth,
        json={
            "question_id": question_id,
            "audio_url": audio_url,
            "video_url": "https://s3.../ans.mp4",
            "is_timeout": is_timeout,
        },
    )
    assert res.status_code == 202, res.json()
    return poll(client, auth, res.json()["task_id"])


def run_session(
    client, auth, *, question_count, persona, answer_for, replay_log=None, is_timeout=False
):
    """세션을 끝까지 돌린다.

    answer_for(item, n)  나간 질문을 보고 이번 답변의 audio_url을 정한다.
    반환값               (세션 시작 응답, 나간 질문 전부 + session_end)
    """
    started = start_session(
        client, auth, question_count=question_count, persona=persona,
        replay_log=replay_log,
    )
    items = [poll(client, auth, started["task_id"])]

    n = 0
    while items[-1]["type"] != "session_end":
        n += 1
        assert n < MAX_TURNS, "세션이 끝나지 않음"
        items.append(
            submit_answer(
                client, auth, started["session_id"],
                items[-1]["question_id"], answer_for(items[-1], n),
                is_timeout=is_timeout,
            )
        )
    return started, items


def always_ok(item, n):
    return OK_AUDIO.format(n=n)


def always_short(item, n):
    return SHORT_AUDIO.format(n=n)


def summarize(items):
    asked = [i for i in items if i["type"] != "session_end"]
    return {
        "문항수": sum(1 for i in asked if i["type"] != "reask"),
        "되묻기": sum(1 for i in asked if i["type"] == "reask"),
        "토픽수": max(i["topic_index"] for i in asked),
    }


def mains(items):
    return [i for i in items if i["type"] == "question"]


# ---------------------------------------------------------------------------
# 시나리오 A — 정상 진행
# ---------------------------------------------------------------------------


def test_시나리오_A_9문항_압박형_전부_충분(client, auth):
    """결과   9문항 · 토픽 4개 · 되묻기 0회"""
    started, items = run_session(
        client, auth, question_count=9, persona="pressure", answer_for=always_ok
    )

    assert summarize(items) == {"문항수": 9, "되묻기": 0, "토픽수": 4}
    assert started["question_total"] == 9
    assert items[-1] == {"type": "session_end", "total_questions": 9}

    asked = items[:-1]
    # 문항 번호가 1부터 빠짐없이 올라간다
    assert [i["question_number"] for i in asked] == list(range(1, 10))
    assert all(i["question_total"] == 9 for i in asked)
    # 잘 답하면 예비 토픽이 투입되지 않는다
    assert all(i["is_spare_topic"] is False for i in asked)
    # 일반 세션에서 is_replay는 항상 false다
    assert all(i["is_replay"] is False for i in asked)
    # 압박형은 세션 마지막 질문이 항상 L3
    assert asked[-1]["difficulty"] == "L3"


# ---------------------------------------------------------------------------
# 시나리오 B — 부실하게 답했다가 회복
# ---------------------------------------------------------------------------


def test_시나리오_B_6문항_친절형_중간에_부실_1회(client, auth):
    """결과   6문항 · 토픽 3개 · 되묻기 1회

    2번 문항만 부실하게 답하고, 되묻기에는 충분히 답해 회복한다.
    되묻기로 회복했으므로 계획한 6문항이 그대로 유지된다.
    """

    def answer_for(item, n):
        # 되묻기(q_2r)에는 충분히 답한다. 주질문 q_2에만 부실하게 답한다.
        if item["type"] != "reask" and item["question_number"] == 2:
            return SHORT_AUDIO.format(n=n)
        return OK_AUDIO.format(n=n)

    started, items = run_session(
        client, auth, question_count=6, persona="friendly", answer_for=answer_for
    )

    assert summarize(items) == {"문항수": 6, "되묻기": 1, "토픽수": 3}
    assert items[-1]["total_questions"] == 6

    (reask,) = [i for i in items if i["type"] == "reask"]
    assert reask["question_id"] == "q_2r"
    assert reask["reask_of"] == "q_2"
    # 되묻기는 문항 수에 세지 않으므로 question_number가 올라가지 않는다
    assert reask["question_number"] == 2
    # 되묻기는 category와 difficulty만 null이며 나머지 필드는 값이 온다
    assert reask["category"] is None
    assert reask["difficulty"] is None
    assert reask["is_spare_topic"] is False
    assert reask["is_replay"] is False
    assert reask["text"] and reask["audio_url"]
    assert reask["question_total"] == 6

    # 회복했으므로 예비 토픽이 투입되지 않는다
    assert all(i["is_spare_topic"] is False for i in items[:-1])


# ---------------------------------------------------------------------------
# 시나리오 C — 계속 부실하게 답함
# ---------------------------------------------------------------------------


def test_시나리오_C_6문항_압박형_전부_부실(client, auth):
    """결과   6문항 · 토픽 6개 · 되묻기 3회

    꼬리질문이 하나도 안 나갔지만 문항 수는 정확히 6개다.
    되묻기는 세션당 3회에서 멈춘다.
    """
    started, items = run_session(
        client, auth, question_count=6, persona="pressure", answer_for=always_short
    )

    assert summarize(items) == {"문항수": 6, "되묻기": 3, "토픽수": 6}
    assert items[-1]["total_questions"] == 6

    asked = items[:-1]
    # 부실하게만 답하면 꼬리질문이 나가지 않는다
    assert not [i for i in asked if i["type"] == "followup"]
    # 계획된 토픽 3개를 소진한 뒤 예비 토픽 3개가 투입된다
    spares = [i for i in asked if i["is_spare_topic"]]
    assert len(spares) == 3
    # 예비 토픽 카테고리는 계획에 쓰인 것과 겹치지 않는다
    planned = [i["category"] for i in mains(asked) if not i["is_spare_topic"]]
    assert not set(i["category"] for i in spares) & set(planned)
    # 되묻기가 나가도 문항 번호는 1..6으로 정확히 올라간다
    assert [i["question_number"] for i in asked if i["type"] != "reask"] == [1, 2, 3, 4, 5, 6]


def test_되묻기는_세션당_3회를_넘지_않는다(client, auth):
    """한도가 없으면 부실하게만 답하는 사용자에게 매 질문마다 되묻게 된다."""
    for question_count in (3, 6, 9):
        for persona in ("friendly", "pressure"):
            _, items = run_session(
                client, auth, question_count=question_count, persona=persona,
                answer_for=always_short,
            )
            s = summarize(items)
            assert s["되묻기"] <= 3, (question_count, persona, s)
            assert s["문항수"] == question_count, (question_count, persona, s)


# ---------------------------------------------------------------------------
# 시나리오 D — 매우 충실하게 답함
#
# 여기만 SessionRunner를 직접 호출한다. 난이도 상향은 verdict="escalate"로만
# 일어나는데 계약서의 답변 제출 요청에는 verdict를 담을 필드가 없다.
# 서버 런타임은 항상 verdict=None을 넘기며, 이 경로는 2단계 LLM 판정을 켤 때 쓴다.
# ---------------------------------------------------------------------------


def plan_with_pattern(question_count, persona, pattern):
    """원하는 배분 패턴이 나오는 plan을 찾는다. seed는 계약서에 없는 값이라 여기서만 쓴다."""
    for seed in range(500):
        plan = build_plan(question_count, persona, seed=seed)
        if plan["pattern"] == pattern:
            return plan
    raise AssertionError(f"패턴 {pattern}을 찾지 못했다")


def drive_runner(plan, verdict_for):
    """러너를 끝까지 돌린다. verdict_for(i)는 i번째 답변의 LLM 판정."""
    runner = SessionRunner(plan)
    log = [runner.start()]
    for i in range(MAX_TURNS):
        item = runner.next(45, 60, verdict=verdict_for(i))
        log.append(item)
        if item["type"] == "session_end":
            return log
    raise AssertionError("세션이 끝나지 않음")


def test_시나리오_D_매우_충실한_답변은_난이도를_올린다():
    """결과   6문항 · 토픽 3개 · 되묻기 0회. 계획이 L2였던 꼬리질문이 L3로 나간다."""
    plan = plan_with_pattern(6, "friendly", [1, 2, 3])  # 점층형

    normal = drive_runner(plan, lambda i: None)
    escalated = drive_runner(plan, lambda i: "escalate" if i == 0 else None)

    # 계획대로면 Q1 L1 · Q2 L1 · Q3 L2 · Q4 L2 · Q5 L3 · Q6 L3
    assert [x["difficulty"] for x in normal[:-1]] == ["L1", "L1", "L2", "L2", "L3", "L3"]

    # 첫 답변이 escalate면 다음 꼬리질문(Q3)이 L2 대신 L3로 나간다
    assert normal[2]["type"] == "followup" and normal[2]["difficulty"] == "L2"
    assert escalated[2]["type"] == "followup" and escalated[2]["difficulty"] == "L3"

    # 상향은 한 번만 쓰이고, 나머지는 계획 그대로다
    assert [x["difficulty"] for x in escalated[:-1]] == ["L1", "L1", "L3", "L2", "L3", "L3"]

    # 충실히 답한다고 꼬리질문이 사라지지 않는다. 문항 수도 토픽 수도 그대로다
    assert [x["type"] for x in escalated] == [x["type"] for x in normal]
    assert escalated[-1]["total"] == 6
    assert not [x for x in escalated if x["type"] == "reask"]


def test_escalate가_없으면_난이도가_올라가지_않는다():
    """서버 런타임은 항상 verdict=None이므로 계획된 난이도가 그대로 나가야 한다."""
    plan = plan_with_pattern(6, "friendly", [1, 2, 3])
    log = drive_runner(plan, lambda i: None)
    assert [x["difficulty"] for x in log[:-1]] == ["L1", "L1", "L2", "L2", "L3", "L3"]


# ---------------------------------------------------------------------------
# 재연습
# ---------------------------------------------------------------------------


def to_replay_log(items):
    """1회차 진행 결과를 백엔드가 보내는 replay_log 형태로 조립한다.

    question   type · text · category · difficulty · is_spare_topic
    followup   type · difficulty
    reask      담지 않는다. 재현하지 않는다
    """
    log = []
    for i in items:
        if i["type"] == "question":
            log.append({
                "type": "question",
                "text": i["text"],
                "category": i["category"],
                "difficulty": i["difficulty"],
                "is_spare_topic": i["is_spare_topic"],
            })
        elif i["type"] == "followup":
            log.append({"type": "followup", "difficulty": i["difficulty"]})
    return log


def test_재연습은_1회차_주질문을_그대로_재생한다(client, auth):
    """주질문이 전부 동일하고 is_replay가 true로 온다."""
    # 1회차 — 중간에 한 번 부실하게 답해 되묻기가 나가게 한다
    def answer_for(item, n):
        if item["type"] != "reask" and item["question_number"] == 2:
            return SHORT_AUDIO.format(n=n)
        return OK_AUDIO.format(n=n)

    _, first = run_session(
        client, auth, question_count=6, persona="friendly", answer_for=answer_for
    )
    assert summarize(first)["되묻기"] == 1

    replay_log = to_replay_log(first)
    # reask는 담기지 않으므로 로그 길이가 문항 수와 같다
    assert len(replay_log) == 6

    # 2회차 — 같은 옵션으로 재연습. 이번에는 전부 충분히 답한다
    started, second = run_session(
        client, auth, question_count=6, persona="friendly",
        answer_for=always_ok, replay_log=replay_log,
    )

    assert summarize(second)["문항수"] == 6
    assert started["question_total"] == 6

    # 주질문이 전부 1회차와 같다 — 텍스트 · 카테고리 · 난이도 · 순서까지
    def signature(items):
        return [
            (i["text"], i["category"], i["difficulty"]) for i in mains(items)
        ]

    assert signature(second) == signature(first)

    # 토픽별 꼬리질문 개수가 1회차와 같다
    def shape(items):
        counts, cur = [], None
        for i in items:
            if i["type"] == "question":
                counts.append(0)
            elif i["type"] == "followup":
                counts[-1] += 1
        return counts

    assert shape(second) == shape(first)

    # 회차 비교 대상은 is_replay가 true인 주질문뿐이다
    assert all(i["is_replay"] is True for i in mains(second))
    assert all(
        i["is_replay"] is False
        for i in second
        if i["type"] in ("followup", "reask")
    )


def test_재연습_2회차에_부실하면_대체_질문으로_문항수를_채운다(client, auth):
    """대체 질문은 1회차에 없던 것이므로 is_replay가 false다."""
    _, first = run_session(
        client, auth, question_count=6, persona="friendly", answer_for=always_ok
    )
    replay_log = to_replay_log(first)

    _, second = run_session(
        client, auth, question_count=6, persona="friendly",
        answer_for=always_short, replay_log=replay_log,
    )

    assert summarize(second)["문항수"] == 6
    substitutes = [i for i in mains(second) if not i["is_replay"]]
    assert substitutes, "꼬리질문을 포기했으면 대체 질문이 나가야 한다"
    # 대체 질문은 회차 비교 대상이 아니다
    assert all(i["is_spare_topic"] is True for i in substitutes)


def test_문항수를_바꾸면_일반_세션으로_처리한다(client, auth):
    """1회차 구조를 재생할 수 없으므로 replay_log를 무시한다."""
    _, first = run_session(
        client, auth, question_count=6, persona="friendly", answer_for=always_ok
    )
    replay_log = to_replay_log(first)

    started, second = run_session(
        client, auth, question_count=9, persona="friendly",
        answer_for=always_ok, replay_log=replay_log,
    )

    assert started["question_total"] == 9
    assert summarize(second)["문항수"] == 9
    # 모든 질문의 is_replay가 false가 된다
    assert all(i["is_replay"] is False for i in second[:-1])


# ---------------------------------------------------------------------------
# 배분 패턴이 무작위여도 결과는 같아야 한다
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("question_count", [3, 6, 9])
@pytest.mark.parametrize("persona", ["friendly", "pressure"])
def test_패턴이_무작위여도_문항수는_항상_맞는다(client, auth, question_count, persona):
    """같은 조건으로 20회 반복해 어떤 배분 패턴이 걸려도 안전한지 확인한다."""
    for _ in range(20):
        for answer_for in (always_ok, always_short):
            _, items = run_session(
                client, auth, question_count=question_count, persona=persona,
                answer_for=answer_for,
            )
            s = summarize(items)
            assert s["문항수"] == question_count, s
            assert items[-1]["total_questions"] == question_count
            assert s["되묻기"] <= 3, s


@pytest.mark.parametrize("question_count", [3, 6, 9])
def test_되묻기는_question_number를_올리지_않는다(client, auth, question_count):
    _, items = run_session(
        client, auth, question_count=question_count, persona="pressure",
        answer_for=always_short,
    )
    for prev, cur in zip(items, items[1:]):
        if cur["type"] == "reask":
            assert cur["question_number"] == prev["question_number"]
            assert cur["reask_of"] == prev["question_id"]
            assert cur["question_id"] == prev["question_id"] + "r"


def test_is_timeout이면_되묻지_않는다(client, auth):
    """시간이 끊은 답변에 되묻는 것은 오작동이다. 답변이 짧아도 그대로 넘어간다."""
    _, items = run_session(
        client, auth, question_count=6, persona="friendly",
        answer_for=always_short, is_timeout=True,
    )
    s = summarize(items)
    assert s["되묻기"] == 0, s
    assert s["문항수"] == 6, s


# ---------------------------------------------------------------------------
# 인증 — 완료 기준 5
# ---------------------------------------------------------------------------


def test_잘못된_시크릿이면_401(client):
    res = client.get("/ai/companies", headers={"X-Cueanda-Secret": "wrong-secret"})
    assert res.status_code == 401
    assert res.json() == {
        "error_code": "UNAUTHORIZED",
        "message": "시크릿 헤더가 없거나 올바르지 않습니다",
    }


def test_시크릿_헤더가_없으면_401(client):
    res = client.post("/ai/sessions", json={})
    assert res.status_code == 401
    assert res.json()["error_code"] == "UNAUTHORIZED"


def test_health와_ready는_시크릿_없이_통과한다(client):
    assert client.get("/health").status_code == 200
    assert client.get("/ready").status_code == 200


def test_verified가_false인_회사는_나오지_않는다(client, auth):
    res = client.get("/ai/companies", headers=auth)
    assert res.status_code == 200
    from ai import companies

    listed = res.json()
    ids = [c["company_id"] for c in listed]
    assert ids, "노출할 기업이 하나도 없습니다"

    # 데이터가 바뀌어도 성립해야 하므로 목록을 고정하지 않고 규칙을 검증한다
    records = {r.company_id: r for r in companies.load_records()}
    assert ids == [cid for cid, r in records.items() if r.verified]
    assert all(not records[cid].verified for cid in records if cid not in ids)

    # 인재상은 응답에 담기지 않는다 (계약서 6장)
    assert all(set(c) == {"company_id", "name", "industry"} for c in listed)
