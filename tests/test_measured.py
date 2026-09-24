"""리포트의 말하기 · 시선 축이 실제 측정값을 쓰는지.

    speech   전사 때 B가 계산한 발화 지표 → metrics. 점수는 SPEECH_SCORER를 꽂으면
    gaze     USE_GAZE=1이면 C의 분석 결과 → gaze_score() → 점수 · 회피 구간 근거

GPU도 네트워크도 쓰지 않는다. 전사와 시선 분석을 가짜로 바꿔 흐름만 본다.
"""
from unittest.mock import patch as mock_patch

import pytest

from ai import answers, gaze, report_dummy
from ai.answers import AnswerText
from ai.gaze import GazeError
from infra.gaze_analysis.gaze_score import gaze_score

from test_report import answer, key, six_answers  # noqa: F401
from test_report_pipeline import make, poll


# 문항별 발화 지표. 평균 · 합계를 손으로 셀 수 있게 값을 고른다.
FLUENCY = {
    1: {"hesitation_score": 20.0, "speech_rate_cv": 0.2, "repetition_count": 1},
    2: {"hesitation_score": 40.0, "speech_rate_cv": 0.4, "repetition_count": 2},
}


@pytest.fixture
def llm_mode(monkeypatch):
    monkeypatch.setenv("AI_MODE", "llm")


@pytest.fixture
def fake_stt():
    def transcribe(*, audio_url, session_id, question_id, is_timeout=False):
        n = int(question_id.split("_")[1].rstrip("r"))
        return AnswerText(
            duration_sec=30 + n,
            word_count=40 + n,
            text=f"{question_id} 실제 답변",
            fluency=FLUENCY[1 if n % 2 else 2],
        )

    with mock_patch.object(answers, "transcribe", side_effect=transcribe) as stt:
        yield stt


# 홀수 문항은 회피 0회, 짝수 문항은 회피 3회
GAZE = {
    1: {"gaze_maintain_ratio": 0.95, "aversion_frequency": 0, "evidence": []},
    2: {
        "gaze_maintain_ratio": 0.80,
        "aversion_frequency": 3,
        "evidence": [{
            "question_id": "",
            "t_start": 4.0, "t_end": 6.5,
            "kind": "weakness", "label": "시선 회피",
            "comment": "4.0초~6.5초 구간에서 화면 밖을 응시했습니다.",
        }],
    },
}


@pytest.fixture
def fake_gaze(monkeypatch):
    monkeypatch.setenv("USE_GAZE", "1")

    def analyze(video_url, question_id):
        n = int(question_id.split("_")[1].rstrip("r"))
        return GAZE[1 if n % 2 else 2]

    with mock_patch.object(gaze, "analyze", side_effect=analyze) as g:
        yield g


@pytest.fixture
def speech_scorer():
    report_dummy.SPEECH_SCORER = lambda m: 100 - int(m["hesitation_score"])
    yield
    report_dummy.SPEECH_SCORER = None


# ---------------------------------------------------------------------------
# 전사 결과에서 지표를 버리지 않는다
# ---------------------------------------------------------------------------


def test_B의_지표를_AnswerText에_담는다():
    heard = answers._to_answer_text({
        "text": "답변", "speech_duration_sec": 12.4, "word_count": 20,
        "hesitation_score": 31.5, "speech_rate_cv": 0.284, "repetition_count": 3,
        "silence_count": 2,
    })
    assert heard.fluency == {
        "hesitation_score": 31.5, "speech_rate_cv": 0.284, "repetition_count": 3,
    }


def test_지표가_빠져_있어도_전사는_성공한다():
    heard = answers._to_answer_text(
        {"text": "답변", "speech_duration_sec": 12.4, "word_count": 20}
    )
    assert heard.text == "답변"
    assert heard.fluency is None


# ---------------------------------------------------------------------------
# 말하기 축
# ---------------------------------------------------------------------------


def test_말하기_metrics가_실제_지표로_채워진다(client, auth, llm_mode, fake_stt):
    result = make(client, auth, six_answers())["result"]
    speech = result["axes"]["speech"]

    # 홀수 3문항 20점, 짝수 3문항 40점 → 평균 30. 반복은 합계 1*3 + 2*3
    assert speech["metrics"] == {
        "hesitation_score": 30,
        "speech_rate_cv": 0.3,
        "repetition_count": 9,
    }
    assert isinstance(speech["metrics"]["hesitation_score"], int), "계약서: 0~100 정수"
    # 구간 단위 근거가 아직 없다. 더미 문장을 붙이면 실제 지표와 어긋난다
    assert speech["evidence"] == []


def test_말하기_점수는_B의_변환식으로_나온다(client, auth, llm_mode, fake_stt):
    """따로 꽂지 않아도 stt.speech_score가 쓰인다.

    머뭇거림 20 → 80점, 40 → 60점. 마무리 감점은 지표가 없어 걸리지 않는다.
    """
    result = make(client, auth, six_answers())["result"]
    assert result["axes"]["speech"]["score"] == 70     # (80*3 + 60*3) / 6
    by_q = {q["question_id"]: q["axes"]["speech"] for q in result["questions"]}
    assert by_q["q_1"] == 80 and by_q["q_2"] == 60


def test_말끝을_흐리면_말하기_점수가_깎인다(client, auth, llm_mode, monkeypatch):
    """마무리 지표를 넘기지 않으면 흐지부지 끝낸 답변과 또박또박 끝낸 답변이
    같은 점수를 받는다. 그것을 막는 테스트다."""
    def transcribe(*, audio_url, session_id, question_id, is_timeout=False):
        trailing = question_id == "q_1"
        return AnswerText(
            duration_sec=30, word_count=40, text=f"{question_id} 답변",
            fluency={
                "hesitation_score": 20.0, "speech_rate_cv": 0.2, "repetition_count": 1,
                "trailing_off": trailing, "silent_ending": False, "is_timeout": False,
            },
        )

    with mock_patch.object(answers, "transcribe", side_effect=transcribe):
        result = make(client, auth, six_answers())["result"]

    by_q = {q["question_id"]: q["axes"]["speech"] for q in result["questions"]}
    assert by_q["q_1"] < by_q["q_2"], "말끝을 흐린 답변이 더 낮아야 합니다"


def test_지표가_없으면_말하기_점수는_더미다(client, auth, llm_mode):
    """전사 결과에 발화 지표가 없을 때다. 억지로 점수를 만들지 않는다."""
    def transcribe(*, audio_url, session_id, question_id, is_timeout=False):
        return AnswerText(duration_sec=30, word_count=40, text="답변")

    with mock_patch.object(answers, "transcribe", side_effect=transcribe):
        result = make(client, auth, six_answers())["result"]

    expected = round(sum(
        report_dummy.score_for("sess_rp", f"q_{n}", "speech") for n in range(1, 7)
    ) / 6)
    assert result["axes"]["speech"]["score"] == expected
    assert result["axes"]["speech"]["metrics"] == {}


def test_변환식을_꽂으면_말하기_점수가_지표에서_나온다(
    client, auth, llm_mode, fake_stt, speech_scorer
):
    result = make(client, auth, six_answers())["result"]
    assert result["axes"]["speech"]["score"] == 70     # (80*3 + 60*3) / 6
    by_q = {q["question_id"]: q["axes"]["speech"] for q in result["questions"]}
    assert by_q["q_1"] == 80 and by_q["q_2"] == 60


def test_문항에_실제_전사와_길이가_들어간다(client, auth, llm_mode, fake_stt):
    result = make(client, auth, six_answers())["result"]
    q1 = result["questions"][0]
    assert q1["transcript"] == "q_1 실제 답변"
    assert q1["duration_sec"] == 31
    assert q1["word_count"] == 41


def test_되묻기_답변은_원_답변에_이어_붙는다(client, auth, llm_mode, fake_stt):
    seen = []
    report_dummy.CONTENT_SCORER = lambda q, a: seen.append(a) or 60
    try:
        rows = six_answers() + [
            answer(1, type="reask", category=None, difficulty=None, reask_of="q_1")
        ]
        result = make(client, auth, rows)["result"]
    finally:
        report_dummy.CONTENT_SCORER = None

    assert "q_1 실제 답변 q_1r 실제 답변" in seen
    q1 = result["questions"][0]
    assert q1["transcript"] == "q_1 실제 답변 q_1r 실제 답변"
    assert q1["duration_sec"] == 31 + 31
    assert q1["had_reask"] is True


def test_더미_모드는_metrics가_비어_있다(client, auth, fake_stt):
    """백엔드가 검증 중인 더미 응답은 그대로여야 한다."""
    result = make(client, auth, six_answers())["result"]
    assert result["axes"]["speech"]["metrics"] == {}
    assert fake_stt.call_count == 0


# ---------------------------------------------------------------------------
# 시선 축
# ---------------------------------------------------------------------------


def test_시선_점수가_C의_변환식으로_나온다(client, auth, llm_mode, fake_stt, fake_gaze):
    result = make(client, auth, six_answers())["result"]
    good, bad = gaze_score(GAZE[1]), gaze_score(GAZE[2])
    assert good > bad, "회피가 있던 답변이 더 낮아야 합니다"

    assert result["axes"]["gaze"]["score"] == round((good * 3 + bad * 3) / 6)
    by_q = {q["question_id"]: q["axes"]["gaze"] for q in result["questions"]}
    assert by_q["q_1"] == good and by_q["q_2"] == bad


def test_시선_근거는_실제_회피_구간이다(client, auth, llm_mode, fake_stt, fake_gaze):
    ev = make(client, auth, six_answers())["result"]["axes"]["gaze"]["evidence"]
    assert len(ev) == 3                                  # 짝수 문항 3개
    assert {e["question_id"] for e in ev} == {"q_2", "q_4", "q_6"}
    assert all(e["t_start"] == 4.0 and e["t_end"] == 6.5 for e in ev)


def test_시선_metrics는_아직_비어_있다(client, auth, llm_mode, fake_stt, fake_gaze):
    """계약서에 키가 합의되지 않았다. 필드를 먼저 넣지 않는다."""
    result = make(client, auth, six_answers())["result"]
    assert result["axes"]["gaze"]["metrics"] == {}


def test_영상이_없는_문항은_시선_평균에서_빠진다(
    client, auth, llm_mode, fake_stt, fake_gaze
):
    rows = six_answers()
    for r in rows[1:]:
        r["video_url"] = None              # 1번만 영상이 있다
    result = make(client, auth, rows)["result"]

    assert fake_gaze.call_count == 1
    assert result["axes"]["gaze"]["score"] == gaze_score(GAZE[1])


def test_시선_분석이_터지면_부분_리포트(client, auth, llm_mode, fake_stt, fake_gaze):
    fake_gaze.side_effect = GazeError("CUDA out of memory")
    body = make(client, auth, six_answers())

    assert body["status"] == "done", "시선 실패로 리포트 전체를 날리면 안 됩니다"
    result = body["result"]
    assert result["report_status"] == "partial"
    assert result["axes"]["gaze"]["status"] == "failed"
    assert result["axes"]["gaze"]["error_code"] == "GAZE_FAILED"
    assert result["overall"]["axes_used"] == ["content", "speech"]


def test_되묻기_영상은_분석하지_않는다(client, auth, llm_mode, fake_stt, fake_gaze):
    rows = six_answers() + [
        answer(1, type="reask", category=None, difficulty=None, reask_of="q_1")
    ]
    make(client, auth, rows)
    assert fake_gaze.call_count == 6


def test_USE_GAZE를_안_켜면_분석하지_않는다(client, auth, llm_mode, fake_stt):
    with mock_patch.object(gaze, "analyze") as g:
        result = make(client, auth, six_answers())["result"]
    assert g.call_count == 0
    assert result["axes"]["gaze"]["status"] == "ok"


def test_가중치_경로가_없으면_GazeError(monkeypatch):
    monkeypatch.delenv("GAZE_WEIGHTS", raising=False)
    with pytest.raises(GazeError):
        gaze.analyze("https://s3.../a.mp4", "q_1")


def test_재시도로_시선만_다시_계산한다(client, auth, llm_mode, fake_stt, fake_gaze):
    res = client.post(
        "/ai/sessions/sess_rp/report/retry",
        headers={**auth, **key(5)},
        json={"axes": ["gaze"], "answers": six_answers()},
    )
    assert res.status_code == 202
    body = poll(client, auth, res.json()["task_id"])

    assert body["status"] == "done"
    good, bad = gaze_score(GAZE[1]), gaze_score(GAZE[2])
    assert body["result"]["axes"]["gaze"]["score"] == round((good + bad) / 2)
    assert body["result"]["axes"]["speech"] is None


def test_재시도로_말하기만_부르면_시선을_분석하지_않는다(
    client, auth, llm_mode, fake_stt, fake_gaze
):
    """시선은 GPU로 몇 분 걸린다. 요청하지 않은 축에 쓰면 안 된다."""
    res = client.post(
        "/ai/sessions/sess_rp/report/retry",
        headers={**auth, **key(6)},
        json={"axes": ["speech"], "answers": six_answers()},
    )
    body = poll(client, auth, res.json()["task_id"])
    assert body["status"] == "done"
    assert fake_gaze.call_count == 0
    assert body["result"]["axes"]["speech"]["metrics"]["hesitation_score"] == 30
