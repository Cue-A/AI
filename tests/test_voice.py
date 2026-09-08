"""질문 음성 합성 이음매.

음성 합성은 외부 API를 호출해 요금이 붙는다. 그래서 LLM과 스위치를 나눴다.
합성이 실패해도 세션은 이어져야 한다 — 질문 텍스트가 이미 있는데 음성
때문에 면접을 끊는 것은 손해가 크다. 계약서 8장이 정한 규칙이다.
"""
import sys
import types

import pytest

from ai import voice


@pytest.fixture
def tts_on(monkeypatch):
    monkeypatch.setenv("USE_TTS", "1")


@pytest.fixture
def fake_tts(monkeypatch):
    """B가 붙일 ai/tts.py를 흉내낸다."""
    calls = []

    module = types.ModuleType("ai.tts")

    def synthesize(text, persona):
        calls.append((text, persona))
        return f"https://s3.../tts/{persona}.mp3"

    module.synthesize = synthesize
    monkeypatch.setitem(sys.modules, "ai.tts", module)
    return calls


# ---------------------------------------------------------------------------
# 스위치
# ---------------------------------------------------------------------------


def test_기본은_꺼져_있다(monkeypatch):
    """모르는 사이에 요금이 나가면 안 된다."""
    monkeypatch.delenv("USE_TTS", raising=False)
    assert voice.tts_enabled() is False
    assert voice.synthesize("질문입니다", "friendly") is None


def test_llm을_켜도_TTS는_따로다(monkeypatch):
    """질문 생성만 확인할 때 음성까지 만들면 그만큼 돈이 나간다."""
    monkeypatch.setenv("AI_MODE", "llm")
    monkeypatch.delenv("USE_TTS", raising=False)
    assert voice.tts_enabled() is False


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "on"])
def test_켜는_값들(monkeypatch, value):
    monkeypatch.setenv("USE_TTS", value)
    assert voice.tts_enabled() is True


# ---------------------------------------------------------------------------
# 합성
# ---------------------------------------------------------------------------


def test_페르소나가_그대로_넘어간다(tts_on, fake_tts):
    """친절형과 압박형이 속도와 톤에서 구분되어야 한다."""
    url = voice.synthesize("어떤 부분을 담당하셨나요?", "pressure")

    assert url == "https://s3.../tts/pressure.mp3"
    assert fake_tts == [("어떤 부분을 담당하셨나요?", "pressure")]


def test_빈_텍스트는_합성하지_않는다(tts_on, fake_tts):
    assert voice.synthesize("   ", "friendly") is None
    assert fake_tts == []


# ---------------------------------------------------------------------------
# 실패해도 세션은 이어진다 — 계약서 8장
# ---------------------------------------------------------------------------


def test_모듈이_없어도_터지지_않는다(tts_on, monkeypatch):
    """B가 아직 안 올렸어도 서버는 돌아야 한다."""
    monkeypatch.setitem(sys.modules, "ai.tts", None)
    assert voice.synthesize("질문입니다", "friendly") is None


def test_함수_이름이_다르면_터지지_않는다(tts_on, monkeypatch):
    module = types.ModuleType("ai.tts")
    module.speak = lambda *a: "x"          # synthesize가 아니다
    monkeypatch.setitem(sys.modules, "ai.tts", module)

    assert voice.synthesize("질문입니다", "friendly") is None


def test_합성이_터져도_예외를_올리지_않는다(tts_on, monkeypatch):
    """예외를 올리면 질문이 이미 만들어졌는데도 세션이 끊긴다."""
    module = types.ModuleType("ai.tts")

    def boom(text, persona):
        raise RuntimeError("API 한도 초과")

    module.synthesize = boom
    monkeypatch.setitem(sys.modules, "ai.tts", module)

    assert voice.synthesize("질문입니다", "friendly") is None


def test_URL이_아니면_버린다(tts_on, monkeypatch):
    module = types.ModuleType("ai.tts")
    module.synthesize = lambda text, persona: b"\x00\x01"   # 바이트를 돌려줬다
    monkeypatch.setitem(sys.modules, "ai.tts", module)

    assert voice.synthesize("질문입니다", "friendly") is None
