"""session_plan.py의 is_timeout 인자 회귀 테스트.

session_plan.py는 원래 수정 금지 파일이다. is_timeout 인자 추가는 예외적으로
합의된 변경이므로, 그 변경이 깨뜨리면 안 되는 불변식을 여기서 잠가둔다.

  is_timeout=False (기본값)  기존 동작 그대로. 부실한 답변이면 되묻는다
  is_timeout=True            되묻기 경로를 아예 타지 않고 reask_used도 소모하지 않는다
  두 경우 모두               문항 수는 정확히 지켜진다
"""
import itertools
import random

import pytest

from ai.session_plan import (
    MAX_REASK_PER_SESSION,
    RetryRunner,
    SessionRunner,
    build_plan,
    topics_from_log,
)

SUFFICIENT = (45, 60)
INSUFFICIENT = (5, 10)

ANSWER_TYPES = {
    "전부충분": lambda i, rng: SUFFICIENT,
    "전부부실": lambda i, rng: INSUFFICIENT,
    "번갈아": lambda i, rng: INSUFFICIENT if i % 2 else SUFFICIENT,
    "무작위": lambda i, rng: INSUFFICIENT if rng.random() < 0.5 else SUFFICIENT,
}

SEEDS = range(20)
COUNTS = (3, 6, 9)
PERSONAS = ("friendly", "pressure")

COMBOS = list(itertools.product(COUNTS, PERSONAS, SEEDS, sorted(ANSWER_TYPES)))


def drive(runner, answer_name, answer_seed, **kwargs):
    """러너를 끝까지 돌리고 로그를 돌려준다. kwargs는 next()로 그대로 넘긴다."""
    rng = random.Random(answer_seed)
    answer_fn = ANSWER_TYPES[answer_name]
    log = [runner.start()]
    for i in itertools.count():
        dur, words = answer_fn(i, rng)
        item = runner.next(dur, words, verdict=None, **kwargs)
        log.append(item)
        if item["type"] == "session_end":
            return log
        assert i < 200, "세션이 끝나지 않음"


def _new_runner(qc, persona, seed):
    return SessionRunner(build_plan(qc, persona, seed=seed))


def _retry_runner(qc, persona, seed):
    """1회차를 전부 충분히 답한 세션으로 만들고 그 구조로 RetryRunner를 만든다."""
    base = drive(_new_runner(qc, persona, seed), "전부충분", seed)
    body = [it for it in base if it["type"] != "session_end"]
    texts = [f"주질문{i + 1}" for i in range(sum(1 for it in body if it["type"] == "question"))]
    return RetryRunner(topics_from_log(body, texts))


# ---------------------------------------------------------------------------


@pytest.mark.parametrize("qc,persona,seed,answers", COMBOS)
def test_is_timeout_기본값은_인자를_넘긴_것과_같다(qc, persona, seed, answers):
    """기본값이 False이므로 기존 호출부와 동작이 같아야 한다."""
    without = drive(_new_runner(qc, persona, seed), answers, seed)
    with_false = drive(_new_runner(qc, persona, seed), answers, seed, is_timeout=False)
    assert without == with_false


@pytest.mark.parametrize("qc,persona,seed,answers", COMBOS)
def test_문항수는_is_timeout과_무관하게_지켜진다(qc, persona, seed, answers):
    for kwargs in ({}, {"is_timeout": False}, {"is_timeout": True}):
        log = drive(_new_runner(qc, persona, seed), answers, seed, **kwargs)
        asked = sum(1 for it in log if it["type"] in ("question", "followup"))
        assert asked == qc, (kwargs, asked)
        assert log[-1]["total"] == qc


@pytest.mark.parametrize("qc,persona,seed,answers", COMBOS)
def test_is_timeout이면_되묻기가_없고_한도도_소모되지_않는다(qc, persona, seed, answers):
    runner = _new_runner(qc, persona, seed)
    log = drive(runner, answers, seed, is_timeout=True)
    assert not any(it["type"] == "reask" for it in log)
    assert runner.reask_used == 0


@pytest.mark.parametrize("qc,persona,seed", list(itertools.product(COUNTS, PERSONAS, SEEDS)))
def test_is_timeout이_False면_부실한_답변에_되묻는다(qc, persona, seed):
    """되묻기 경로가 살아 있는지 확인한다. 세션당 한도는 3회다."""
    runner = _new_runner(qc, persona, seed)
    log = drive(runner, "전부부실", seed, is_timeout=False)
    reasks = [it for it in log if it["type"] == "reask"]
    assert reasks, "전부 부실하게 답했는데 되묻기가 한 번도 없다"
    assert runner.reask_used == len(reasks)
    assert runner.reask_used <= MAX_REASK_PER_SESSION


@pytest.mark.parametrize("qc,persona,seed,answers", COMBOS)
def test_RetryRunner도_동일하다(qc, persona, seed, answers):
    without = drive(_retry_runner(qc, persona, seed), answers, seed)
    with_false = drive(_retry_runner(qc, persona, seed), answers, seed, is_timeout=False)
    assert without == with_false

    runner = _retry_runner(qc, persona, seed)
    expected = runner.target
    log = drive(runner, answers, seed, is_timeout=True)
    asked = sum(1 for it in log if it["type"] in ("question", "followup"))
    assert asked == expected
    assert not any(it["type"] == "reask" for it in log)
    assert runner.reask_used == 0
