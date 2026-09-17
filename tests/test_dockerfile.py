"""배포 이미지에 필요한 파일이 들어가는지.

로컬과 테스트에서는 저장소 전체가 보여서 안 드러나고, 이미지로 빌드해야만
드러나는 문제를 막는다. 둘 다 에러 없이 품질만 떨어지는 유형이다.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOCKERFILE = (ROOT / "Dockerfile").read_text(encoding="utf-8")


def _stage(name: str) -> str:
    start = DOCKERFILE.index(f"AS {name}")
    rest = DOCKERFILE[start:]
    nxt = rest.find("\nFROM ", 1)
    return rest if nxt == -1 else rest[:nxt]


def test_full_이미지에_채점_프롬프트가_들어간다():
    """빠지면 한 줄짜리 기본 프롬프트로 조용히 채점해서
    유창한 딴소리가 높은 점수를 받는다."""
    assert "COPY docs/" in _stage("full")
    assert (ROOT / "docs" / "내용채점_프롬프트_초안.md").is_file()


def test_full_이미지에_infra가_들어간다():
    """ai/tts.py가 S3 업로드를 infra/에서 가져온다.
    빠지면 음성 합성이 조용히 꺼지고 텍스트만 나간다."""
    assert "COPY infra/" in _stage("full")


def test_dummy_이미지는_가볍게_유지한다():
    """백엔드가 로컬에서 띄우는 이미지다. 실제 서비스용 파일은 넣지 않는다."""
    dummy = _stage("dummy")
    assert "COPY docs/" not in dummy
    assert "COPY infra/" not in dummy
    assert "requirements-full" not in dummy
