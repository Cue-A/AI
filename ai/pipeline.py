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

from ai import answers, companies, dummy, llm, resume, tasks
from ai.answers import SttError
from ai.dummy import DummySession
from ai.llm import Exchange, LlmError
from ai.resume import ResumeError
from ai.schemas import AnswerSubmitRequest, SessionCreateRequest, TaskDoneResponse
from ai.session_plan import RetryRunner
from ai.tasks import BackgroundTask, TaskFailed

logger = logging.getLogger("cue.ai.pipeline")

# 세션 시작에는 STT가 없다. 답변이 아직 없기 때문이다.
# tts는 음성 합성이 붙으면 실제 단계가 된다.
SESSION_START_STAGES = ("generating", "tts")

# 답변 처리는 전사부터 시작한다. (계약서 4장)
ANSWER_STAGES = ("stt", "generating", "tts")

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
        job_role=req.job_role,
    )

    if not llm.llm_enabled():
        # 더미는 즉시 계산한다. DUMMY_POLL_TICKS로 processing을 흉내낼 수 있다.
        return session, dummy.save_task(session.start())

    task = tasks.run(
        lambda t: _prepare(t, session, req), stages=SESSION_START_STAGES
    )
    logger.info("세션 %s 시작 — 주질문 생성을 백그라운드로 돌립니다", session.session_id)
    return session, dummy.register_task(task)


# ---------------------------------------------------------------------------
# 답변 처리
#
#   답변 오디오  →  전사  →  길이 게이트  →  다음 항목
#                            └ 꼬리질문이면 직전 답변을 읽고 새로 만든다
#
# 더미 모드는 이 흐름을 타지 않는다. audio_url 문자열로 길이를 지어내고
# 즉시 결과를 낸다. 백엔드가 지금 검증하고 있는 동작이 그것이다.
# ---------------------------------------------------------------------------


def _history(session: DummySession) -> list[Exchange]:
    return [
        Exchange(question=e["question"], answer=e["answer"])
        for e in session.answered_history()
    ]


def _reask_text(session: DummySession) -> Optional[str]:
    """무엇을 더 말해야 하는지 짚어 주는 되묻기. 만들지 못하면 None.

    고정 문장 하나로는 지원자가 두 번째에도 같은 대답을 한다.
    """
    history = _history(session)
    if not history:
        return None

    try:
        return llm.generate_reask(
            history=history, persona=session.persona, job_role=session.job_role
        )
    except LlmError as e:
        logger.warning("되묻기 생성 실패 — 고정 문장으로 대신합니다: %s", e)
        return None


def _followup_text(session: DummySession, difficulty: str, job_role: str) -> Optional[str]:
    # job_role은 세션이 들고 있다. 계약서 요청 필드를 그대로 보관한 것이다.
    """직전 답변을 파고드는 꼬리질문. 만들지 못하면 None을 준다.

    실패해도 세션을 멈추지 않는다. 고정 문장이 나가는 편이
    면접이 중간에 끊기는 것보다 낫다.
    """
    history = _history(session)
    if not history:
        # STT가 텍스트를 주지 못했다. 근거 없이 꼬리질문을 만들 수는 없다.
        return None

    try:
        return llm.generate_followup(
            history=history,
            difficulty=difficulty,
            persona=session.persona,
            job_role=job_role,
        )
    except LlmError as e:
        logger.warning("꼬리질문 생성 실패 — 고정 문장으로 대신합니다: %s", e)
        return None


def _handle_answer(task: BackgroundTask, session: DummySession, req: AnswerSubmitRequest):
    """백그라운드 본체 — 전사하고 다음 항목을 낸다."""
    task.set_stage("stt")
    try:
        heard = answers.transcribe(
            audio_url=req.audio_url,
            session_id=session.session_id,
            question_id=req.question_id,
            is_timeout=req.is_timeout,
        )
    except SttError as e:
        raise TaskFailed("STT_FAILED", str(e)) from e

    task.set_stage("generating")
    result = session.advance(
        heard.duration_sec,
        heard.word_count,
        is_timeout=req.is_timeout,
        answer_text=heard.text,
    )

    # 주질문은 세션 시작 때 이미 만들어 뒀다. 꼬리질문과 되묻기만 여기서 만든다.
    # LLM이 꺼져 있으면(USE_STT만 켠 경우) 고정 문장이 그대로 나간다.
    kind = getattr(result, "type", None) if llm.llm_enabled() else None
    if kind == "followup":
        text = _followup_text(session, result.difficulty, session.job_role)
        if text:
            result = result.model_copy(update={"text": text})
    elif kind == "reask":
        text = _reask_text(session)
        if text:
            result = result.model_copy(update={"text": text})

    task.set_stage("tts")
    return TaskDoneResponse(status="done", result=result)


def submit_answer(session: DummySession, req: AnswerSubmitRequest) -> str:
    """답변을 받아 다음 항목 작업을 띄운다. task_id를 준다."""
    if not answers.stt_enabled():
        return dummy.save_task(session.answer(req.audio_url, is_timeout=req.is_timeout))

    task = tasks.run(lambda t: _handle_answer(t, session, req), stages=ANSWER_STAGES)
    return dummy.register_task(task)
