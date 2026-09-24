"""질문 음성을 백엔드가 정한 S3 경로에 올리는지.

    sessions/{sessionId}/questions/{questionId}.mp3      (백엔드 docs/20-storage.md)

AI 서버의 S3 쓰기 권한은 이 경로로만 열린다. 다른 곳에 올리면 실제 S3에서
권한 거부로 실패해 질문 음성이 전부 사라진다.

Typecast · S3는 부르지 않는다. boto3도 가짜로 끼운다(로컬에 없을 수 있다).
"""
import importlib
import sys
import types

import pytest

from ai import voice

from test_pipeline import (  # noqa: F401
    _answer, _first_question, fake_llm, llm_mode,
)


@pytest.fixture
def s3(monkeypatch, tmp_path):
    """boto3를 가짜로 끼우고 올라간 경로를 모은다."""
    uploads = []

    class FakeClient:
        def upload_file(self, local_path, bucket, key, ExtraArgs=None):
            uploads.append({"bucket": bucket, "key": key, "type": (ExtraArgs or {}).get("ContentType")})

    boto3 = types.ModuleType("boto3")
    boto3.client = lambda *a, **k: FakeClient()
    botocore = types.ModuleType("botocore")
    exceptions = types.ModuleType("botocore.exceptions")
    exceptions.ClientError = type("ClientError", (Exception,), {})
    botocore.exceptions = exceptions
    monkeypatch.setitem(sys.modules, "boto3", boto3)
    monkeypatch.setitem(sys.modules, "botocore", botocore)
    monkeypatch.setitem(sys.modules, "botocore.exceptions", exceptions)

    import ai as ai_pkg
    import infra.gaze_analysis as gaze_pkg

    for name in ("infra.gaze_analysis.s3_upload", "ai.tts"):
        monkeypatch.delitem(sys.modules, name, raising=False)
    # 불러오면 패키지 속성으로도 붙는다. `from ai import tts`는 그걸 먼저 보므로
    # 테스트가 끝나면 함께 떼어야 뒤 테스트의 가짜 tts가 먹힌다.
    monkeypatch.delattr(ai_pkg, "tts", raising=False)
    monkeypatch.delattr(gaze_pkg, "s3_upload", raising=False)
    s3_upload = importlib.import_module("infra.gaze_analysis.s3_upload")
    monkeypatch.setattr(s3_upload, "S3_BUCKET", "cue-a-media")
    monkeypatch.setattr(s3_upload, "_s3_client", None)

    tts = importlib.import_module("ai.tts")
    monkeypatch.setattr(tts, "TYPECAST_API_KEY", "test-key")
    monkeypatch.setattr(tts, "CACHE_DIR", str(tmp_path))
    typecast_calls = []
    monkeypatch.setattr(
        tts, "_call_typecast",
        lambda text, persona: typecast_calls.append(text) or b"ID3-fake-mp3",
    )
    yield types.SimpleNamespace(
        uploads=uploads, typecast=typecast_calls, tts=tts, s3_upload=s3_upload
    )
    for name in ("infra.gaze_analysis.s3_upload", "ai.tts"):
        sys.modules.pop(name, None)
    for pkg, attr in ((ai_pkg, "tts"), (gaze_pkg, "s3_upload")):
        if hasattr(pkg, attr):
            delattr(pkg, attr)


# ---------------------------------------------------------------------------
# 경로
# ---------------------------------------------------------------------------


def test_경로는_백엔드_규칙과_같다(s3):
    assert s3.s3_upload.question_audio_key("sess_9f2a1c", "q_1") == \
        "sessions/sess_9f2a1c/questions/q_1.mp3"


def test_질문_음성을_세션_경로에_올린다(s3):
    url = s3.tts.synthesize("지원 동기가 무엇인가요?", "friendly",
                            session_id="sess_a", question_id="q_1")

    assert s3.uploads == [{
        "bucket": "cue-a-media",
        "key": "sessions/sess_a/questions/q_1.mp3",
        "type": "audio/mpeg",
    }]
    assert url.endswith("/sessions/sess_a/questions/q_1.mp3")


def test_번호가_없으면_예전처럼_임의_경로다(s3):
    """번호를 안 넘기는 호출도 깨지지 않게 둔다."""
    s3.tts.synthesize("질문입니다", "friendly")
    assert s3.uploads[0]["key"].startswith("tts/")


# ---------------------------------------------------------------------------
# 요금 — 같은 문장은 다시 합성하지 않는다
# ---------------------------------------------------------------------------


def test_다른_세션의_같은_문장은_다시_합성하지_않는다(s3):
    """재연습은 1회차 주질문을 그대로 다시 읽는다. Typecast를 또 부르면 요금이 나간다.
    경로만 달라지므로 업로드만 새로 한다."""
    s3.tts.synthesize("지원 동기가 무엇인가요?", "friendly", session_id="sess_a", question_id="q_1")
    s3.tts.synthesize("지원 동기가 무엇인가요?", "friendly", session_id="sess_b", question_id="q_1")

    assert s3.typecast == ["지원 동기가 무엇인가요?"]
    assert [u["key"] for u in s3.uploads] == [
        "sessions/sess_a/questions/q_1.mp3",
        "sessions/sess_b/questions/q_1.mp3",
    ]


def test_같은_질문을_다시_부르면_올리지도_않는다(s3):
    first = s3.tts.synthesize("질문", "pressure", session_id="sess_a", question_id="q_2")
    again = s3.tts.synthesize("질문", "pressure", session_id="sess_a", question_id="q_2")

    assert first == again
    assert len(s3.uploads) == 1
    assert len(s3.typecast) == 1


def test_페르소나가_다르면_다시_합성한다(s3):
    s3.tts.synthesize("질문", "friendly", session_id="sess_a", question_id="q_1")
    s3.tts.synthesize("질문", "pressure", session_id="sess_b", question_id="q_1")
    assert len(s3.typecast) == 2


# ---------------------------------------------------------------------------
# voice.py — 번호 전달과 예전 모양 호환
# ---------------------------------------------------------------------------


@pytest.fixture
def tts_on(monkeypatch):
    monkeypatch.setenv("USE_TTS", "1")


def test_voice가_세션과_질문_번호를_넘긴다(tts_on, monkeypatch):
    calls = []
    module = types.ModuleType("ai.tts")

    def synthesize(text, persona, session_id=None, question_id=None):
        calls.append((session_id, question_id))
        return "https://s3.../x.mp3"

    module.synthesize = synthesize
    monkeypatch.setitem(sys.modules, "ai.tts", module)

    voice.synthesize("질문", "friendly", session_id="sess_a", question_id="q_3")
    assert calls == [("sess_a", "q_3")]


def test_번호를_안_받는_예전_tts도_그대로_돈다(tts_on, monkeypatch):
    module = types.ModuleType("ai.tts")
    module.synthesize = lambda text, persona: "https://s3.../old.mp3"
    monkeypatch.setitem(sys.modules, "ai.tts", module)

    assert voice.synthesize("질문", "friendly", session_id="sess_a", question_id="q_3") \
        == "https://s3.../old.mp3"


def test_면접_중_질문마다_번호가_넘어간다(client, auth, tts_on, llm_mode, fake_llm, monkeypatch):
    """세션 시작 → 첫 질문 → 답변 → 다음 질문까지 번호가 제대로 붙는지."""
    calls = []
    module = types.ModuleType("ai.tts")

    def synthesize(text, persona, session_id=None, question_id=None):
        calls.append((session_id, question_id))
        return f"https://s3.../sessions/{session_id}/questions/{question_id}.mp3"

    module.synthesize = synthesize
    monkeypatch.setitem(sys.modules, "ai.tts", module)

    sid, first = _first_question(client, auth)
    done, _ = _answer(client, auth, sid, first)
    second = done["result"]

    assert calls == [(sid, first["question_id"]), (sid, second["question_id"])]
    assert first["audio_url"].endswith(f"sessions/{sid}/questions/{first['question_id']}.mp3")
