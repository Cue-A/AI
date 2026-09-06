"""백그라운드 실행.

LLM을 붙이면 세션 시작이 10~30초 걸린다. 그동안 폴링하면 processing이 나가고,
끝나면 최종 응답이 나가야 한다. 실패해도 processing에 갇히면 안 된다.
"""
import threading

import pytest

from ai import tasks
from ai.schemas import SessionEndResult, TaskDoneResponse
from ai.tasks import BackgroundTask, TaskFailed

STAGES = ("stt", "generating", "tts")


def done_payload(total=6):
    return TaskDoneResponse(
        status="done", result=SessionEndResult(type="session_end", total_questions=total)
    )


# ---------------------------------------------------------------------------
# 진행과 완료
# ---------------------------------------------------------------------------


def test_끝나기_전에는_processing():
    started = threading.Event()
    release = threading.Event()

    def work(task):
        started.set()
        release.wait(5)
        return done_payload()

    task = tasks.run(work, stages=STAGES)
    assert started.wait(5)

    body = task.poll()
    assert body.status == "processing"
    assert body.stage == "stt"          # 첫 단계로 시작한다
    assert not task.finished

    release.set()
    assert task.wait(5).status == "done"


def test_단계를_알리면_폴링에_반영된다():
    seen = []
    gate = threading.Event()

    def work(task):
        for stage in STAGES:
            task.set_stage(stage)
            seen.append(task.poll().stage)
        gate.wait(5)
        return done_payload()

    task = tasks.run(work, stages=STAGES)
    while len(seen) < 3:
        pass
    gate.set()
    task.wait(5)
    assert seen == list(STAGES)


def test_끝난_뒤에는_계속_최종_응답():
    task = tasks.run(lambda t: done_payload(9), stages=STAGES)
    task.wait(5)
    for _ in range(3):
        body = task.poll()
        assert body.status == "done"
        assert body.result.total_questions == 9


# ---------------------------------------------------------------------------
# 실패
# ---------------------------------------------------------------------------


def test_TaskFailed는_계약서_에러코드로_나간다():
    def work(task):
        raise TaskFailed("RESUME_PARSE_FAILED", "이력서를 내려받지 못했습니다")

    body = tasks.run(work, stages=STAGES).wait(5)
    assert body.status == "error"
    assert body.error_code == "RESUME_PARSE_FAILED"
    assert body.message == "이력서를 내려받지 못했습니다"


def test_예상_못_한_오류도_error로_끝난다():
    """processing에 갇히면 백엔드는 타임아웃까지 기다렸다 원인도 모른 채 실패한다."""
    def work(task):
        raise ZeroDivisionError("버그")

    body = tasks.run(work, stages=STAGES).wait(5)
    assert body.status == "error"
    assert body.error_code == "LLM_FAILED"


def test_실패해도_폴링이_멈추지_않는다():
    task = tasks.run(lambda t: 1 / 0, stages=STAGES)
    task.wait(5)
    assert task.poll().status == "error"
    assert task.finished


# ---------------------------------------------------------------------------
# 리포트 폴링 — progress
# ---------------------------------------------------------------------------


REPORT_STAGES = (
    "transcribing", "analyzing_speech", "analyzing_gaze",
    "analyzing_content", "composing",
)


def test_리포트는_progress가_붙는다():
    gate = threading.Event()
    seen = []

    def work(task):
        for stage in REPORT_STAGES:
            task.set_stage(stage)
            body = task.poll()
            seen.append((body.stage, body.progress))
        gate.wait(5)
        return done_payload()

    task = tasks.run(work, stages=REPORT_STAGES, with_progress=True)
    while len(seen) < len(REPORT_STAGES):
        pass
    gate.set()
    task.wait(5)

    assert [s for s, _ in seen] == list(REPORT_STAGES)
    progresses = [p for _, p in seen]
    assert progresses == sorted(progresses)      # 단조증가
    assert all(0 < p < 1 for p in progresses)


def test_질문_폴링에는_progress가_없다():
    gate = threading.Event()
    holder = {}

    def work(task):
        holder["body"] = task.poll()
        gate.wait(5)
        return done_payload()

    task = tasks.run(work, stages=STAGES)
    while "body" not in holder:
        pass
    gate.set()
    task.wait(5)
    assert set(holder["body"].model_dump()) == {"status", "stage"}


# ---------------------------------------------------------------------------
# 동시성
# ---------------------------------------------------------------------------


def test_여러_작업이_동시에_돈다():
    gate = threading.Event()

    def work(n):
        def inner(task):
            gate.wait(5)
            return done_payload(n)
        return inner

    running = [tasks.run(work(n), stages=STAGES) for n in (3, 6, 9)]
    assert all(t.poll().status == "processing" for t in running)

    gate.set()
    assert [t.wait(5).result.total_questions for t in running] == [3, 6, 9]


def test_stages가_비면_거부한다():
    with pytest.raises(ValueError):
        BackgroundTask(stages=())


def test_기다려도_안_끝나면_TimeoutError():
    gate = threading.Event()
    task = tasks.run(lambda t: gate.wait(10) or done_payload(), stages=STAGES)
    with pytest.raises(TimeoutError):
        task.wait(0.2)
    gate.set()
