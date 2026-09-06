"""세션 시작 흐름.

    세션 생성 (즉시)  →  202 응답
    이력서 다운로드   ┐
    주질문 생성       ├ 백그라운드. 폴링하면 processing이 나간다
    첫 질문 발행      ┘

AI_MODE가 dummy면 백그라운드를 타지 않고 고정 문장으로 즉시 끝난다.
재연습도 LLM을 부르지 않는다 — 1회차 주질문 텍스트를 그대로 재생하기 때문이다.
"""
import logging
from typing import Optional

from ai import companies, dummy, llm, resume, tasks
from ai.dummy import DummySession
from ai.llm import LlmError
from ai.resume import ResumeError
from ai.schemas import SessionCreateRequest, TaskDoneResponse
from ai.session_plan import RetryRunner
from ai.tasks import BackgroundTask, TaskFailed

logger = logging.getLogger("cue.ai.pipeline")

# 세션 시작에는 STT가 없다. 답변이 아직 없기 때문이다.
# tts는 3주차에 음성 합성이 붙으면 실제 단계가 된다.
SESSION_START_STAGES = ("generating", "tts")

# 예비 토픽의 주질문 난이도. session_plan.levels_for_topic이 예비 토픽에는
# 페르소나와 무관하게 L2를 주므로 그 값을 그대로 쓴다.
SPARE_DIFFICULTY = "L2"


def main_question_slots(session: DummySession) -> list[tuple[str, str]]:
    """생성해야 할 (카테고리, 난이도) 목록.

    계획된 토픽과 예비 토픽을 모두 담는다. 예비 토픽은 세션 도중에 투입되는데,
    그때 다시 생성하면 그만큼 사용자를 기다리게 하기 때문이다.

    재연습은 빈 목록이다. 1회차 주질문을 텍스트까지 그대로 재생한다.
    """
    runner = session.runner
    if isinstance(runner, RetryRunner):
        return []

    plan = runner.plan
    slots = [
        (category, levels[0])
        for category, levels in zip(plan["categories"], plan["difficulty"])
    ]
    slots += [(c, SPARE_DIFFICULTY) for c in plan["spare_categories"]]
    return slots


def _prepare(task: BackgroundTask, session: DummySession, req: SessionCreateRequest):
    """백그라운드 본체 — 이력서를 읽고 주질문을 만든 뒤 첫 질문을 낸다."""
    task.set_stage("generating")

    slots = main_question_slots(session)
    if slots:
        try:
            resume_file = resume.fetch(req.resume_file_url)
        except ResumeError as e:
            raise TaskFailed("RESUME_PARSE_FAILED", str(e)) from e

        try:
            generated = llm.generate_main_questions(
                resume=resume_file,
                job_role=req.job_role,
                persona=req.persona,
                slots=slots,
                company_profile=_company_profile(req),
            )
        except LlmError as e:
            raise TaskFailed("LLM_FAILED", str(e)) from e

        session.attach_main_questions(generated)

    # TTS는 3주차에 붙는다. 지금은 단계만 지나간다.
    task.set_stage("tts")
    return TaskDoneResponse(status="done", result=session.start())


def _company_profile(req: SessionCreateRequest) -> Optional[str]:
    """질문에 반영할 인재상.

    직접 입력값이 company_id보다 우선한다 (계약서 2장).
    둘 다 없으면 None이고, 그러면 직무만으로 질문을 만든다.
    """
    return req.company_profile_override or companies.profile_for(
        req.company_id, req.job_role
    )


def start_session(req: SessionCreateRequest) -> tuple[DummySession, str]:
    """세션을 만들고 첫 질문 작업을 띄운다. (세션, task_id)

    세션 자체는 즉시 만들어지므로 session_id와 question_total을 바로 응답할 수 있다.
    """
    session = dummy.create_session(
        question_count=req.question_count,
        persona=req.persona,
        replay_log=req.replay_log,
    )

    if not llm.llm_enabled():
        # 더미는 즉시 계산한다. DUMMY_POLL_TICKS로 processing을 흉내낼 수 있다.
        return session, dummy.save_task(session.start())

    task = tasks.run(
        lambda t: _prepare(t, session, req), stages=SESSION_START_STAGES
    )
    logger.info("세션 %s 시작 — 주질문 생성을 백그라운드로 돌립니다", session.session_id)
    return session, dummy.register_task(task)
