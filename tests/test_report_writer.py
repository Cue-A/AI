"""리포트 글 칸 — 내용 근거 · 개선 답변 · 기업 코멘트 · 압박 대응력 · 회차 비교.

Claude는 부르지 않는다. report_dummy.WRITER_CALL에 가짜를 꽂는다.
핵심은 두 가지다.

  1. 답변에 없는 말은 내보내지 않는다 (지어낸 발췌는 버린다)
  2. Claude가 실패해도 리포트는 나간다 (가짜 문장 대신 빈 칸)
"""
from unittest.mock import patch as mock_patch

import pytest

from ai import answers, report_dummy, report_writer
from ai.answers import AnswerText
from ai.report_writer import Answer, _Evidence, _Improved, _Written, locate

from test_report import answer, key, report_body  # noqa: F401
from test_report_pipeline import make, poll


# 단어별 시간이 있는 답변. 「중복 결제를 막았습니다」는 3.0초~5.2초에 말했다.
TEXT = "결제 모듈을 맡았습니다. 중복 결제를 막았습니다."
WORDS = [
    {"text": "결제", "start": 0.0, "end": 0.4},
    {"text": "모듈을", "start": 0.5, "end": 1.0},
    {"text": "맡았습니다.", "start": 1.1, "end": 2.0},
    {"text": "중복", "start": 3.0, "end": 3.4},
    {"text": "결제를", "start": 3.5, "end": 4.1},
    {"text": "막았습니다.", "start": 4.2, "end": 5.2},
]
REASK_TEXT = "재시도 횟수를 세 번으로 제한했습니다."
REASK_WORDS = [
    {"text": "재시도", "start": 0.3, "end": 0.8},
    {"text": "횟수를", "start": 0.9, "end": 1.3},
    {"text": "세", "start": 1.4, "end": 1.5},
    {"text": "번으로", "start": 1.6, "end": 2.0},
    {"text": "제한했습니다.", "start": 2.1, "end": 3.0},
]


# ---------------------------------------------------------------------------
# locate — 구절이 말해진 구간 찾기
# ---------------------------------------------------------------------------


def test_구절이_말해진_구간을_찾는다():
    assert locate("중복 결제를 막았습니다", WORDS) == (3.0, 5.2)


def test_띄어쓰기와_문장부호가_달라도_찾는다():
    """Whisper는 띄어쓰기를 제멋대로 넣고, Claude는 인용할 때 문장부호를 바꾼다."""
    assert locate("중복결제를 막았습니다!", WORDS) == (3.0, 5.2)


def test_단어_중간에서_시작해도_그_단어부터_잡는다():
    assert locate("듈을 맡았", WORDS) == (0.5, 2.0)


def test_없는_구절이면_None():
    assert locate("캐시를 도입했습니다", WORDS) is None


def test_단어_시간이_없으면_None():
    assert locate("중복 결제", None) is None
    assert locate("중복 결제", []) is None


# ---------------------------------------------------------------------------
# write — 결과 검증
# ---------------------------------------------------------------------------


def ans(qid="q_1", text=TEXT, words=WORDS, score=55, kind="question", reasks=()):
    return Answer(question_id=qid, question="맡은 역할은?", category="프로젝트경험",
                  kind=kind, text=text, content_score=score, duration_sec=6.0,
                  words=words, reasks=list(reasks))


def fake(written: _Written):
    calls = []

    def call(system, user):
        calls.append(user)
        return written

    call.calls = calls
    return call


@pytest.fixture(autouse=True)
def _clean():
    report_writer.reset()
    yield
    report_writer.reset()


def test_근거에_실제_구간이_붙는다():
    raw = _Written(evidence=[_Evidence(question_id="q_1", kind="strength", label="구체적 행동",
                                        comment="중복 결제를 막은 방법을 말했습니다.",
                                        quote="중복 결제를 막았습니다")],
                   improved_answers=[])
    out = report_writer.write("s", "friendly", "백엔드", None, [ans()], call=fake(raw))
    (e,) = out.evidence
    assert (e.question_id, e.t_start, e.t_end) == ("q_1", 3.0, 5.2)
    assert e.kind == "strength" and e.label == "구체적 행동"


def test_구절을_못_찾은_근거는_답변_전체_구간으로_둔다():
    """근거 문장 자체는 쓸모가 있다. 구간만 넓게 잡는다."""
    raw = _Written(evidence=[_Evidence(question_id="q_1", kind="weakness", label="수치 없음",
                                        comment="성과 수치가 없습니다.", quote="지어낸 말")],
                   improved_answers=[])
    (e,) = report_writer.write("s", "friendly", "백엔드", None, [ans()], call=fake(raw)).evidence
    assert (e.t_start, e.t_end) == (0.0, 6.0)


def test_지어낸_발췌의_개선_답변은_버린다():
    """원문 발췌는 사용자가 자기 말로 읽는 부분이다. 없는 말이면 내보내지 않는다."""
    raw = _Written(evidence=[], improved_answers=[
        _Improved(question_id="q_1", quote="캐시를 도입해 속도를 올렸습니다", suggestion="수치를 넣으세요."),
        _Improved(question_id="q_1", quote="결제 모듈을 맡았습니다", suggestion="맡은 범위를 구체적으로 말해 보세요."),
    ])
    out = report_writer.write("s", "friendly", "백엔드", None, [ans()], call=fake(raw))
    (imp,) = out.improved_answers
    assert imp.excerpt == "결제 모듈을 맡았습니다"
    assert (imp.t_start, imp.t_end) == (0.0, 2.0)


def test_되묻기_답변에서_따온_구절은_되묻기_번호와_구간으로():
    reask = ans(qid="q_1r", text=REASK_TEXT, words=REASK_WORDS)
    raw = _Written(evidence=[], improved_answers=[
        _Improved(question_id="q_1", quote="세 번으로 제한했습니다", suggestion="왜 세 번인지 근거를 말해 보세요."),
    ])
    (imp,) = report_writer.write("s", "friendly", "백엔드", None,
                                 [ans(reasks=[reask])], call=fake(raw)).improved_answers
    assert imp.question_id == "q_1r"
    assert (imp.t_start, imp.t_end) == (1.4, 3.0)


def test_문항당_근거는_2개까지():
    ev = [_Evidence(question_id="q_1", kind="weakness", label=f"약점{i}",
                    comment="설명", quote="결제") for i in range(4)]
    out = report_writer.write("s", "friendly", "백엔드", None, [ans()],
                              call=fake(_Written(evidence=ev, improved_answers=[])))
    assert len(out.evidence) == 2


def test_없는_문항의_근거는_버린다():
    ev = [_Evidence(question_id="q_9", kind="weakness", label="x", comment="설명", quote="결제")]
    out = report_writer.write("s", "friendly", "백엔드", None, [ans()],
                              call=fake(_Written(evidence=ev, improved_answers=[])))
    assert out.evidence == []


def test_분류명은_12자로_자른다():
    ev = [_Evidence(question_id="q_1", kind="weakness", label="아주아주긴분류명이라서잘려야합니다",
                    comment="설명", quote="결제")]
    (e,) = report_writer.write("s", "friendly", "백엔드", None, [ans()],
                               call=fake(_Written(evidence=ev, improved_answers=[]))).evidence
    assert len(e.label) <= 12


def test_인재상을_안_보냈으면_기업_코멘트를_버린다():
    raw = _Written(evidence=[], improved_answers=[], company_comment="도전 정신이 드러났습니다.")
    assert report_writer.write("s", "friendly", "백엔드", None, [ans()],
                               call=fake(raw)).company_comment is None
    assert report_writer.write("s2", "friendly", "백엔드", "도전 — 먼저 시도한다", [ans()],
                               call=fake(raw)).company_comment == "도전 정신이 드러났습니다."


def test_Claude가_실패해도_예외를_올리지_않는다():
    def boom(system, user):
        raise RuntimeError("API 장애")

    out = report_writer.write("s", "friendly", "백엔드", None, [ans()], call=boom)
    assert out.evidence == [] and out.improved_answers == [] and out.company_comment is None


def test_같은_입력이면_다시_부르지_않는다():
    """재시도(시선만) 때 코멘트 요금이 또 나가지 않게."""
    call = fake(_Written(evidence=[], improved_answers=[]))
    report_writer.write("s", "friendly", "백엔드", None, [ans()], call=call)
    report_writer.write("s", "friendly", "백엔드", None, [ans()], call=call)
    assert len(call.calls) == 1


def test_프롬프트에_점수와_답변이_들어간다():
    call = fake(_Written(evidence=[], improved_answers=[]))
    report_writer.write("s", "pressure", "백엔드", "도전", [ans(score=42)], call=call)
    (user,) = call.calls
    assert "내용 점수 42" in user and TEXT in user and "압박형" in user and "도전" in user


# ---------------------------------------------------------------------------
# 리포트에 실제로 들어가는지
# ---------------------------------------------------------------------------


@pytest.fixture
def llm_mode(monkeypatch):
    monkeypatch.setenv("AI_MODE", "llm")


@pytest.fixture
def stt_with_words():
    def transcribe(*, audio_url, session_id, question_id, is_timeout=False):
        return AnswerText(duration_sec=6, word_count=6, text=TEXT, words=WORDS)

    with mock_patch.object(answers, "transcribe", side_effect=transcribe):
        yield


@pytest.fixture
def writer():
    raw = _Written(
        evidence=[_Evidence(question_id="q_1", kind="strength", label="구체적 행동",
                            comment="방법을 말했습니다.", quote="중복 결제를 막았습니다")],
        improved_answers=[_Improved(question_id="q_2", quote="결제 모듈을 맡았습니다",
                                    suggestion="맡은 범위를 구체적으로 말해 보세요.")],
        company_comment="도전 정신이 드러났습니다.",
    )
    call = fake(raw)
    report_dummy.WRITER_CALL = call
    report_dummy.CONTENT_SCORER = lambda q, a: 70
    yield call
    report_dummy.WRITER_CALL = None
    report_dummy.CONTENT_SCORER = None


def rows_with_followups():
    """주질문 · 꼬리질문이 섞인 면접. 압박 대응력을 잴 수 있다."""
    return [
        answer(1), answer(2, type="followup", difficulty="L2"),
        answer(3), answer(4, type="followup", difficulty="L3"),
    ]


def test_리포트에_실제_코멘트가_들어간다(client, auth, llm_mode, stt_with_words, writer):
    body = client.post("/ai/sessions/sess_w/report", headers={**auth, **key(1)},
                       json={**report_body(rows_with_followups()),
                             "company_profile_override": "도전 — 먼저 시도한다"})
    result = poll(client, auth, body.json()["task_id"])["result"]

    (e,) = result["axes"]["content"]["evidence"]
    assert (e["question_id"], e["t_start"], e["t_end"]) == ("q_1", 3.0, 5.2)
    (imp,) = result["improved_answers"]
    assert imp["original_excerpt"] == "결제 모듈을 맡았습니다"
    assert "(더미)" not in imp["original_excerpt"]
    assert result["company_comment"] == "도전 정신이 드러났습니다."
    assert len(writer.calls) == 1


def test_코멘트_생성이_실패해도_리포트는_나간다(client, auth, llm_mode, stt_with_words):
    def boom(system, user):
        raise RuntimeError("API 장애")

    report_dummy.WRITER_CALL = boom
    report_dummy.CONTENT_SCORER = lambda q, a: 70
    try:
        body = make(client, auth, rows_with_followups())
    finally:
        report_dummy.WRITER_CALL = None
        report_dummy.CONTENT_SCORER = None

    assert body["status"] == "done"
    result = body["result"]
    assert result["axes"]["content"]["score"] == 70
    assert result["axes"]["content"]["evidence"] == []      # 가짜 문장 대신 빈 칸
    assert result["improved_answers"] == []


def test_더미_모드는_고정_문장_그대로다(client, auth):
    """백엔드가 검증 중인 더미 응답은 바뀌면 안 된다."""
    result = make(client, auth, rows_with_followups())["result"]
    assert result["improved_answers"][0]["original_excerpt"] == "(더미) 답변 일부 발췌"


def test_요금_스위치가_꺼져_있으면_코멘트를_만들지_않는다(client, auth, llm_mode, stt_with_words):
    """채점기만 꽂고 WRITER_CALL은 안 꽂은 경우. 진짜 Claude를 부르면 안 된다."""
    report_dummy.CONTENT_SCORER = lambda q, a: 70
    try:
        body = make(client, auth, rows_with_followups())
    finally:
        report_dummy.CONTENT_SCORER = None
    assert body["status"] == "done"


# ---------------------------------------------------------------------------
# 압박 대응력
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("main,follow,expected", [
    (80, 80, 100),    # 떨어지지 않음
    (80, 90, 100),    # 꼬리질문이 더 좋음
    (80, 75, 90),     # 5점 하락
    (80, 55, 50),     # 25점 하락
    (90, 20, 0),      # 70점 하락 → 0
])
def test_압박_대응력은_꼬리질문_하락폭으로_잰다(
    client, auth, llm_mode, stt_with_words, writer, main, follow, expected
):
    def scorer(question_text, answer_text):
        return follow if "꼬리" in question_text else main

    report_dummy.CONTENT_SCORER = scorer
    rows = [answer(1), answer(2, type="followup"), answer(3), answer(4, type="followup")]
    for r in rows:
        if r["type"] == "followup":
            r["text"] = f"꼬리 {r['text']}"
    result = make(client, auth, rows)["result"]

    res = result["resilience"]
    assert res["score"] == expected
    assert f"주질문 {main}점" in res["comment"]


def test_친절형은_압박_대응력이_없다(client, auth, llm_mode, stt_with_words, writer):
    body = client.post("/ai/sessions/sess_f/report", headers={**auth, **key(3)},
                       json=report_body(rows_with_followups(), persona="friendly"))
    assert poll(client, auth, body.json()["task_id"])["result"]["resilience"] is None


def test_꼬리질문이_없으면_압박_대응력을_잴_수_없다(client, auth, llm_mode, stt_with_words, writer):
    result = make(client, auth, [answer(1), answer(2), answer(3)])["result"]
    assert result["resilience"] is None


# ---------------------------------------------------------------------------
# 재시도 — 코멘트를 다시 만들지 않는다
# ---------------------------------------------------------------------------


def test_시선만_재시도하면_코멘트를_다시_만들지_않는다(client, auth, llm_mode, stt_with_words, writer):
    make(client, auth, rows_with_followups())
    res = client.post("/ai/sessions/sess_rp/report/retry", headers={**auth, **key(9)},
                      json={**report_body(rows_with_followups()), "axes": ["gaze"]})
    body = poll(client, auth, res.json()["task_id"])
    assert body["status"] == "done"
    assert len(writer.calls) == 1


# ---------------------------------------------------------------------------
# 회차 비교 코멘트 — 숫자로 만든다
# ---------------------------------------------------------------------------


def test_회차_비교_코멘트는_점수_변화를_말한다():
    from ai.report_dummy import _question_comment, _trend_comment, _vs_comment

    assert _vs_comment(6, {"content": 8, "speech": 5, "gaze": -2}) == \
        "직전 회차보다 총점이 6점 올랐습니다. (내용 +8, 말하기 +5, 시선 -2)"
    assert _vs_comment(0, {"content": 0, "speech": 0, "gaze": 0}) == "직전 회차와 총점이 같습니다."
    assert _trend_comment({"content": [55, 63, 74], "speech": [60, 60, 60], "gaze": [None, 61, 63]},
                          ["speech"]) == \
        "첫 회차부터 지금까지 내용 55→74, 시선 61→63. 최근 3회차 동안 말하기 점수가 정체돼 있습니다."
    assert _question_comment(7, 16, 4) == "직전 회차보다 7점 올랐습니다. 1회차 대비 +16점입니다."
    assert _question_comment(0, 0, 1) == "비교할 이전 회차가 없습니다."
