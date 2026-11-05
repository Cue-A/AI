"""무음 환각 방지 (ai/stt.py) 테스트.

실서비스 테스트에서 60초 동안 아무 말도 안 한 답변이 "감사합니다." x4로 전사되어
내용 평가가 잘못된 것을 확인하고 넣은 방어 로직을 검증한다. 모델은 불러오지 않는다.
"""

import types

from ai import stt

W = types.SimpleNamespace


def _seg(text, words, no_speech_prob=0.1, avg_logprob=-0.3):
    return W(
        text=text,
        words=[W(word=w, start=s, end=e) for w, s, e in words],
        no_speech_prob=no_speech_prob,
        avg_logprob=avg_logprob,
    )


NORMAL = _seg(
    " 저는 침묵 구간을 기준으로 지표를 만들었습니다.",
    [(" 저는", 0.0, 0.5), (" 침묵", 0.6, 1.0), (" 구간을", 1.1, 1.5),
     (" 기준으로", 1.6, 2.0), (" 지표를", 2.1, 2.5), (" 만들었습니다.", 2.6, 3.2)],
)


def test_인사말만_있는_전사는_빈_답변이_된다():
    hallucinated = [_seg(" 감사합니다.", [(" 감사합니다.", i * 15, i * 15 + 1)]) for i in range(4)]
    assert stt._drop_hallucinated_segments(hallucinated) == []


def test_시청_인사_환각도_지운다():
    hallucinated = [_seg(" 시청해 주셔서 감사합니다.", [(" 시청해", 0, 1), (" 주셔서", 1, 2), (" 감사합니다.", 2, 3)])]
    assert stt._drop_hallucinated_segments(hallucinated) == []


def test_정상_답변_끝의_감사_인사는_남긴다():
    thanks = _seg(" 감사합니다.", [(" 감사합니다.", 4.0, 4.8)])
    assert stt._drop_hallucinated_segments([NORMAL, thanks]) == [NORMAL, thanks]


def test_말소리_없음_확률이_높고_확신도가_낮은_세그먼트는_버린다():
    noisy = _seg(" 음 그", [(" 음", 10.0, 10.2), (" 그", 10.3, 10.5)], no_speech_prob=0.9, avg_logprob=-1.5)
    assert stt._drop_hallucinated_segments([NORMAL, noisy]) == [NORMAL]


def test_한쪽_조건만_맞으면_버리지_않는다():
    # 작게 말해서 no_speech_prob만 높게 나온 경우까지 지우지 않도록 두 조건을 모두 본다
    quiet = _seg(" 그렇습니다", [(" 그렇습니다", 5.0, 5.8)], no_speech_prob=0.9, avg_logprob=-0.4)
    assert stt._drop_hallucinated_segments([NORMAL, quiet]) == [NORMAL, quiet]


def test_신뢰도_속성이_없는_세그먼트는_그대로_둔다():
    plain = W(text=" 안녕하세요", words=[])
    assert stt._drop_hallucinated_segments([plain]) == [plain]


def test_무응답이면_말하기_점수는_0점():
    assert stt.speech_score({"no_speech": True, "word_count": 0, "hesitation_score": 0.0}) == 0
    assert stt.speech_score({"word_count": 0, "hesitation_score": 0.0, "is_timeout": True}) == 0


def test_예전_캐시_결과는_기존_방식대로_계산한다():
    # word_count · no_speech 키가 없는 과거 캐시
    assert stt.speech_score({"hesitation_score": 20.0, "trailing_off": False}) == 80


def test_무음_답변은_빈_전사와_no_speech로_반환된다(monkeypatch, tmp_path):
    monkeypatch.setattr(stt, "CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(stt, "transcribe_core", lambda path: ([], 60.0))

    result = stt.process_answer("silent.webm", "sess_test", "q_2", is_timeout=True)

    assert result["text"] == ""
    assert result["word_count"] == 0
    assert result["no_speech"] is True
    assert result["words"] == []
    assert stt.speech_score(result) == 0


def test_정상_답변은_no_speech가_false():
    sm = stt.compute_speech_metrics([NORMAL])
    assert sm["word_count"] > 0


# ---------------------------------------------------------------------------
# 리포트 말하기 축 — 무응답이면 0점
# 단어가 없으면 머뭇거림이 0으로 계산돼 85점(마무리 감점만 빠진 값)이 나오던 문제
# ---------------------------------------------------------------------------

from ai.answers import AnswerText  # noqa: E402
from ai.report_dummy import Measured, _speech_score  # noqa: E402

FLUENCY_SILENT = {
    "hesitation_score": 0.0, "speech_rate_cv": 0.0, "repetition_count": 0,
    "trailing_off": True, "silent_ending": True, "is_timeout": False,
}


def _measured(word_count):
    heard = AnswerText(duration_sec=0, word_count=word_count, text="", fluency=FLUENCY_SILENT)
    return Measured(heard={"q_2": heard})


def test_무응답_문항의_리포트_말하기_점수는_0점():
    row = W(question_id="q_2")
    assert _speech_score(row, _measured(0)) == 0


def test_말한_문항은_기존_변환식을_그대로_쓴다():
    row = W(question_id="q_2")
    assert _speech_score(row, _measured(12)) == stt.speech_score(FLUENCY_SILENT)


def test_측정값이_없으면_기존처럼_더미_점수로_넘긴다():
    row = W(question_id="q_2")
    assert _speech_score(row, None) is None


# ---------------------------------------------------------------------------
# 필러 처리 — Whisper가 "음.."을 지우든 받아쓰든 같은 점수
# VAD 비교에서 q3_short의 머뭇거림이 26.4 → 16.5로 흔들린 문제 (중간 "음.."이 단어로 잡힘)
# ---------------------------------------------------------------------------

def _q3_like(with_filler: bool):
    """'좋은 개발자가 되고 싶습니다. 실력도 키우고 싶고 (음..) 그렇습니다. 감사합니다.'"""
    first = _seg(" 좋은 개발자가 되고 싶습니다. 실력도 키우고 싶고",
                 [(" 좋은", 0.5, 0.8), (" 개발자가", 0.9, 1.4), (" 되고", 1.5, 1.7), (" 싶습니다.", 1.8, 2.3),
                  (" 실력도", 2.6, 3.0), (" 키우고", 3.1, 3.5), (" 싶고", 3.6, 3.9)])
    filler = _seg(" 음..", [(" 음..", 4.6, 5.3)])
    last = _seg(" 그렇습니다. 감사합니다.", [(" 그렇습니다.", 6.2, 6.8), (" 감사합니다.", 7.0, 7.6)])
    return [first, filler, last] if with_filler else [first, last]


def _hesitation(segments, duration=8.0):
    sm = stt.compute_speech_metrics(segments)
    em = stt.compute_ending_metrics(sm["text"], segments, duration)
    fm = stt.compute_fluency_metrics(segments, sm["silence_segments"], sm["speech_duration_sec"])
    return sm, em, fm


def test_필러를_받아써도_지운_것과_같은_지표가_나온다():
    sm_a, em_a, fm_a = _hesitation(_q3_like(with_filler=False))
    sm_b, em_b, fm_b = _hesitation(_q3_like(with_filler=True))

    assert sm_a["word_count"] == sm_b["word_count"] == 9
    assert sm_a["silence_segments"] == sm_b["silence_segments"]
    assert sm_b["silence_total_sec"] == 2.3  # 3.9 → 6.2, 필러 시간이 침묵으로 들어감
    assert fm_a == fm_b
    assert em_a == em_b
    assert sm_b["text"].count("음") == 1  # 전사 텍스트에는 그대로 남는다


def test_필러_반복은_반복_횟수로_세지_않는다():
    segs = [_seg(" 음 음 저는", [(" 음", 0, 0.3), (" 음", 0.4, 0.7), (" 저는", 0.8, 1.2)])]
    assert stt.compute_fluency_metrics(segs, [], 1.2)["repetition_count"] == 0


def test_끝에_붙은_필러는_종결_판정에서_뺀다():
    segs = [_seg(" 열심히 하겠습니다. 음..", [(" 열심히", 0, 0.5), (" 하겠습니다.", 0.6, 1.4), (" 음..", 2.0, 2.6)])]
    em = stt.compute_ending_metrics(" 열심히 하겠습니다. 음..", segs, 3.0)
    assert em["is_concluded"] is True
    assert em["trailing_silence_sec"] == 1.6  # 마지막 실제 단어(1.4초) 뒤부터


def test_필러로_보지_않는_말():
    for token in ["그", "저는", "음식", "어디", "아니요", "에너지", "네"]:
        assert not stt._is_filler(token), token
    for token in ["음", "음..", "어,", "으음", "흠", "아."]:
        assert stt._is_filler(token), token


def test_필러만_말한_답변은_무응답으로_본다():
    segs = [_seg(" 음... 어...", [(" 음...", 1, 2), (" 어...", 5, 6)])]
    sm = stt.compute_speech_metrics(segs)
    assert sm["word_count"] == 0
    assert stt.speech_score({**sm, "no_speech": sm["word_count"] == 0}) == 0
