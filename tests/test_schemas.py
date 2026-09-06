"""계약서의 JSON 예시가 그대로 파싱되는지 확인한다.

여기 나오는 JSON은 docs/질문생성_API계약_백엔드전달용.md 와
docs/질문 유형.md 12장에서 그대로 옮긴 것이다.
예시를 수정해서 통과시키면 안 된다. 모델을 고쳐야 한다.
"""
import json

import pytest
from pydantic import ValidationError

from ai.schemas import (
    CATEGORIES,
    AbortResponse,
    AnswerSubmitRequest,
    AnswerSubmitResponse,
    CompanyOut,
    CompanyRecord,
    ErrorResponse,
    QuestionResult,
    ReplayLogItem,
    SessionCreateRequest,
    SessionCreateResponse,
    SessionEndResult,
    TaskDoneResponse,
    TaskErrorResponse,
    TaskProcessingResponse,
)
from ai.session_plan import SLOT_POOLS, SPARE_POOL


# ---------------------------------------------------------------------------
# 2. 세션 시작
# ---------------------------------------------------------------------------


def test_계약서_2장_세션시작_요청():
    raw = """
    {
      "resume_file_url": "https://s3.../resume_abc.pdf",
      "job_role": "백엔드 개발",
      "persona": "pressure",
      "company_id": "hyundai_enc",
      "company_profile_override": null,
      "question_count": 9,
      "retry_of_session_id": null,
      "doc_id": null
    }
    """
    req = SessionCreateRequest.model_validate(json.loads(raw))

    assert req.resume_file_url == "https://s3.../resume_abc.pdf"
    assert req.job_role == "백엔드 개발"
    assert req.persona == "pressure"
    assert req.company_id == "hyundai_enc"
    assert req.company_profile_override is None
    assert req.question_count == 9
    assert req.retry_of_session_id is None
    assert req.doc_id is None
    # 예시에 없는 필드는 기본값으로 채워진다
    assert req.replay_log is None


def test_question_count_기본값은_6():
    req = SessionCreateRequest.model_validate(
        {
            "resume_file_url": "https://s3.../r.pdf",
            "job_role": "백엔드 개발",
            "persona": "friendly",
        }
    )
    assert req.question_count == 6


@pytest.mark.parametrize("bad", [1, 4, 5, 12, "6"])
def test_question_count는_3_6_9만_받는다(bad):
    with pytest.raises(ValidationError):
        SessionCreateRequest.model_validate(
            {
                "resume_file_url": "https://s3.../r.pdf",
                "job_role": "백엔드 개발",
                "persona": "friendly",
                "question_count": bad,
            }
        )


def test_persona는_friendly_pressure만_받는다():
    with pytest.raises(ValidationError):
        SessionCreateRequest.model_validate(
            {
                "resume_file_url": "https://s3.../r.pdf",
                "job_role": "백엔드 개발",
                "persona": "친절형",
            }
        )


def test_계약서_2장_재연습_요청():
    """재연습 예시는 부분 발췌라 일반 세션 필수 필드를 함께 넣어 검증한다."""
    raw = """
    {
      "question_count": 6,
      "retry_of_session_id": "sess_abc",
      "replay_log": [
        { "type": "question", "text": "백엔드 개발 직무에 지원하신 이유를 말씀해 주세요.",
          "category": "지원동기", "difficulty": "L1", "is_spare_topic": false },
        { "type": "question", "text": "가장 자신 있는 기술 스택은 무엇인가요?",
          "category": "직무역량", "difficulty": "L1", "is_spare_topic": false },
        { "type": "followup", "difficulty": "L2" },
        { "type": "question", "text": "팀원과 의견이 갈렸던 경험을 말씀해 주세요.",
          "category": "협업·갈등", "difficulty": "L2", "is_spare_topic": false },
        { "type": "followup", "difficulty": "L3" },
        { "type": "followup", "difficulty": "L3" }
      ]
    }
    """
    payload = json.loads(raw)
    payload |= {
        "resume_file_url": "https://s3.../resume_abc.pdf",
        "job_role": "백엔드 개발",
        "persona": "friendly",
    }
    req = SessionCreateRequest.model_validate(payload)

    assert req.retry_of_session_id == "sess_abc"
    assert req.replay_log is not None and len(req.replay_log) == 6

    first = req.replay_log[0]
    assert first.type == "question"
    assert first.category == "지원동기"
    assert first.difficulty == "L1"
    assert first.is_spare_topic is False

    # followup은 difficulty만 있으면 된다. text·category·is_spare_topic은 null이다
    fu = req.replay_log[2]
    assert fu.type == "followup"
    assert fu.difficulty == "L2"
    assert fu.text is None
    assert fu.category is None
    assert fu.is_spare_topic is None


def test_replay_log_카테고리는_가운뎃점까지_일치해야_한다():
    ReplayLogItem.model_validate(
        {
            "type": "question",
            "text": "t",
            "category": "협업·갈등",
            "difficulty": "L2",
            "is_spare_topic": False,
        }
    )
    with pytest.raises(ValidationError):
        ReplayLogItem.model_validate(
            {
                "type": "question",
                "text": "t",
                "category": "협업",  # 가운뎃점 없이 보내면 거부한다
                "difficulty": "L2",
                "is_spare_topic": False,
            }
        )


def test_replay_log_reask는_받지_않는다():
    """reask는 배열에서 제외한다. 재현하지 않는다. (계약서 2장)"""
    with pytest.raises(ValidationError):
        ReplayLogItem.model_validate({"type": "reask", "difficulty": None})


def test_계약서_2장_세션시작_응답():
    raw = """
    {
      "session_id": "sess_9f2a1c",
      "task_id": "task_001",
      "question_total": 9
    }
    """
    res = SessionCreateResponse.model_validate(json.loads(raw))
    assert res.session_id == "sess_9f2a1c"
    assert res.task_id == "task_001"
    assert res.question_total == 9


# ---------------------------------------------------------------------------
# 3. 답변 제출
# ---------------------------------------------------------------------------


def test_계약서_3장_답변제출_요청():
    raw = """
    {
      "question_id": "q_1",
      "audio_url": "https://s3.../ans_1.webm",
      "video_url": "https://s3.../ans_1.mp4",
      "is_timeout": false
    }
    """
    req = AnswerSubmitRequest.model_validate(json.loads(raw))
    assert req.question_id == "q_1"
    assert req.audio_url == "https://s3.../ans_1.webm"
    assert req.video_url == "https://s3.../ans_1.mp4"
    assert req.is_timeout is False


def test_video_url은_카메라_미사용이면_null():
    req = AnswerSubmitRequest.model_validate(
        {
            "question_id": "q_1",
            "audio_url": "https://s3.../ans_1.webm",
            "video_url": None,
            "is_timeout": False,
        }
    )
    assert req.video_url is None


def test_is_timeout은_필수():
    with pytest.raises(ValidationError):
        AnswerSubmitRequest.model_validate(
            {"question_id": "q_1", "audio_url": "https://s3.../ans_1.webm"}
        )


def test_계약서_3장_답변제출_응답():
    res = AnswerSubmitResponse.model_validate(json.loads('{ "task_id": "task_002" }'))
    assert res.task_id == "task_002"


# ---------------------------------------------------------------------------
# 4. 작업 상태 조회
# ---------------------------------------------------------------------------


def test_계약서_4장_진행중():
    res = TaskProcessingResponse.model_validate(
        json.loads('{ "status": "processing", "stage": "stt" }')
    )
    assert res.status == "processing"
    assert res.stage == "stt"


@pytest.mark.parametrize("stage", ["stt", "generating", "tts"])
def test_stage_3종(stage):
    assert TaskProcessingResponse.model_validate(
        {"status": "processing", "stage": stage}
    ).stage == stage


def test_계약서_4장_완료_질문():
    raw = """
    {
      "status": "done",
      "result": {
        "type": "question",
        "question_id": "q_4",
        "text": "왜 낙관적 락을 선택하셨나요?",
        "audio_url": "https://s3.../q_4.mp3",
        "category": "프로젝트경험",
        "difficulty": "L2",
        "question_number": 4,
        "question_total": 9,
        "topic_index": 2,
        "topic_total": 4,
        "is_spare_topic": false,
        "is_replay": false
      }
    }
    """
    res = TaskDoneResponse.model_validate(json.loads(raw))
    r = res.result
    assert isinstance(r, QuestionResult)
    assert r.type == "question"
    assert r.question_id == "q_4"
    assert r.text == "왜 낙관적 락을 선택하셨나요?"
    assert r.audio_url == "https://s3.../q_4.mp3"
    assert r.category == "프로젝트경험"
    assert r.difficulty == "L2"
    assert r.question_number == 4
    assert r.question_total == 9
    assert r.topic_index == 2
    assert r.topic_total == 4
    assert r.is_spare_topic is False
    assert r.is_replay is False
    # 예시에 표기되지 않은 reask_of는 null로 채워진다
    assert r.reask_of is None


def test_계약서_4장_완료_되묻기():
    raw = """
    {
      "status": "done",
      "result": {
        "type": "reask",
        "question_id": "q_4r",
        "reask_of": "q_4",
        "text": "어떤 기술을 사용하셨는지 조금 더 말씀해 주시겠어요?",
        "audio_url": "https://s3.../q_4r.mp3",
        "category": null,
        "difficulty": null,
        "question_number": 4,
        "question_total": 9,
        "topic_index": 2,
        "topic_total": 4,
        "is_spare_topic": false,
        "is_replay": false
      }
    }
    """
    res = TaskDoneResponse.model_validate(json.loads(raw))
    r = res.result
    assert isinstance(r, QuestionResult)
    assert r.type == "reask"
    assert r.reask_of == "q_4"
    # 되묻기는 category와 difficulty만 null이며 나머지 필드는 값이 온다
    assert r.category is None
    assert r.difficulty is None
    assert r.question_number == 4
    assert r.is_spare_topic is False
    assert r.is_replay is False


def test_계약서_4장_완료_세션종료():
    raw = """
    {
      "status": "done",
      "result": {
        "type": "session_end",
        "total_questions": 9
      }
    }
    """
    res = TaskDoneResponse.model_validate(json.loads(raw))
    assert isinstance(res.result, SessionEndResult)
    assert res.result.total_questions == 9


def test_계약서_4장_실패():
    raw = """
    {
      "status": "error",
      "error_code": "STT_FAILED",
      "message": "음성을 인식하지 못했습니다"
    }
    """
    res = TaskErrorResponse.model_validate(json.loads(raw))
    assert res.error_code == "STT_FAILED"
    assert res.message == "음성을 인식하지 못했습니다"


def test_audio_url은_TTS_실패시_null():
    r = QuestionResult.model_validate(
        {
            "type": "question",
            "question_id": "q_1",
            "text": "t",
            "audio_url": None,
            "category": "지원동기",
            "difficulty": "L1",
            "question_number": 1,
            "question_total": 6,
            "topic_index": 1,
            "topic_total": 3,
            "is_spare_topic": False,
            "is_replay": False,
        }
    )
    assert r.audio_url is None


def test_is_spare_topic_is_replay는_nullable이_아니다():
    """모든 응답에 두 필드가 포함되므로 nullable 처리는 필요 없다. (계약서 4장)"""
    base = {
        "type": "question",
        "question_id": "q_1",
        "text": "t",
        "audio_url": None,
        "category": "지원동기",
        "difficulty": "L1",
        "question_number": 1,
        "question_total": 6,
        "topic_index": 1,
        "topic_total": 3,
        "is_spare_topic": False,
        "is_replay": False,
    }
    for field in ("is_spare_topic", "is_replay"):
        with pytest.raises(ValidationError):
            QuestionResult.model_validate(base | {field: None})
        with pytest.raises(ValidationError):
            QuestionResult.model_validate({k: v for k, v in base.items() if k != field})


@pytest.mark.parametrize("bad", ["L0", "L4", "l1", "1"])
def test_difficulty는_L1_L2_L3만(bad):
    with pytest.raises(ValidationError):
        QuestionResult.model_validate(
            {
                "type": "question",
                "question_id": "q_1",
                "text": "t",
                "audio_url": None,
                "category": "지원동기",
                "difficulty": bad,
                "question_number": 1,
                "question_total": 6,
                "topic_index": 1,
                "topic_total": 3,
                "is_spare_topic": False,
                "is_replay": False,
            }
        )


# ---------------------------------------------------------------------------
# 6. 회사 목록 / 7. 세션 중단 / 8. 에러
# ---------------------------------------------------------------------------


def test_계약서_6장_회사목록():
    raw = """
    [
      { "company_id": "hyundai_enc", "name": "현대건설(주)", "industry": "종합건설 · 플랜트" }
    ]
    """
    items = [CompanyOut.model_validate(x) for x in json.loads(raw)]
    assert items[0].company_id == "hyundai_enc"
    assert items[0].name == "현대건설(주)"
    assert items[0].industry == "종합건설 · 플랜트"


def test_회사_응답에_verified가_노출되지_않는다():
    record = CompanyRecord.model_validate(
        {
            "company_id": "hyundai_enc",
            "name": "현대건설(주)",
            "industry": "종합건설 · 플랜트",
            "verified": True,
        }
    )
    out = CompanyOut.model_validate(record.model_dump())
    assert "verified" not in out.model_dump()


def test_계약서_7장_세션중단():
    res = AbortResponse.model_validate(json.loads('{ "status": "aborted" }'))
    assert res.status == "aborted"


@pytest.mark.parametrize(
    "code",
    [
        "INVALID_REQUEST",
        "UNAUTHORIZED",
        "SESSION_NOT_FOUND",
        "SESSION_ENDED",
        "INVALID_QUESTION_ID",
        "INVALID_CATEGORY",
        "RESUME_PARSE_FAILED",
        "STT_FAILED",
        "LLM_FAILED",
        "TTS_FAILED",
    ],
)
def test_에러코드_10종(code):
    assert ErrorResponse(error_code=code, message="x").error_code == code


def test_error_code는_null이_될_수_없다():
    """백엔드가 분기하는 기준이므로 항상 값이 있어야 한다.

    계약서 8장에 코드가 없는 일반 검증 오류에는 INVALID_REQUEST를 쓴다.
    """
    with pytest.raises(ValidationError):
        ErrorResponse(message="replay_log가 필요합니다")
    with pytest.raises(ValidationError):
        ErrorResponse(error_code=None, message="x")
    assert ErrorResponse(
        error_code="INVALID_REQUEST", message="replay_log가 필요합니다"
    ).error_code == "INVALID_REQUEST"


# ---------------------------------------------------------------------------
# 빠지기 쉬운 필드 · 카테고리 문자열 일치
# ---------------------------------------------------------------------------


def test_빠지기_쉬운_필드가_전부_있다():
    assert {"reask_of", "is_spare_topic", "is_replay", "question_number",
            "question_total"} <= set(QuestionResult.model_fields)
    assert {"video_url", "is_timeout"} <= set(AnswerSubmitRequest.model_fields)
    assert {"replay_log", "company_profile_override", "doc_id"} <= set(
        SessionCreateRequest.model_fields
    )


def test_카테고리_8종():
    assert len(CATEGORIES) == 8
    assert set(CATEGORIES) == {
        "지원동기", "직무역량", "프로젝트경험", "문제해결",
        "협업·갈등", "실패·성장", "가치관·인성", "미래계획",
    }


def test_카테고리_문자열이_session_plan과_일치한다():
    """가운뎃점 코드포인트까지 같아야 한다. 다르면 세션 진행 결과를 응답에 못 싣는다."""
    used = {c for pool in SLOT_POOLS.values() for c in pool} | set(SPARE_POOL)
    assert used <= set(CATEGORIES), used - set(CATEGORIES)
    assert "·" in "협업·갈등"  # U+00B7 MIDDLE DOT
