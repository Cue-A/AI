"""코드가 가져다 쓰는 외부 패키지가 requirements에 다 있는지.

개발자 컴퓨터에는 다른 이유로 깔려 있어서 안 드러나고, 새 서버에 설치할 때만
ImportError로 드러나는 문제를 막는다. requests가 이렇게 빠져 있었다.
"""
import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# import 이름 → requirements에 적힌 패키지 이름
PACKAGE_OF = {
    "anthropic": "anthropic",
    "boto3": "boto3",
    "botocore": "boto3",  # boto3가 같이 깐다
    "celery": "celery",
    "fastapi": "fastapi",
    "starlette": "fastapi",  # fastapi가 같이 깐다
    "faster_whisper": "faster-whisper",
    "httpx2": "httpx2",
    "pydantic": "pydantic",
    "redis": "redis",
    "requests": "requests",
}

# GPU 서버에서 CUDA 버전에 맞춰 따로 설치한다 (requirements-full.txt 주석 참고)
GPU_INSTALLED = {"cv2", "torch", "l2cs"}

# 저장소 안의 모듈
LOCAL = {"ai", "infra", "main", "celery_app", "extract_l2cs_warm", "gaze_analysis",
         "gaze_metrics", "gaze_score", "s3_upload"}


def _imports() -> set[str]:
    names = set()
    files = [ROOT / "main.py", *(ROOT / "ai").rglob("*.py"), *(ROOT / "infra").rglob("*.py")]
    for f in files:
        tree = ast.parse(f.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names.add(node.module.split(".")[0])
    return names


def _required() -> set[str]:
    pkgs = set()
    for name in ("requirements-base.txt", "requirements-full.txt"):
        for line in (ROOT / name).read_text(encoding="utf-8").splitlines():
            line = line.split("#")[0].strip()
            if not line or line.startswith("-"):
                continue
            for sep in ("==", ">=", "<", "[", ">", "~="):
                line = line.split(sep)[0]
            pkgs.add(line.strip().lower())
    return pkgs


def test_외부_패키지가_requirements에_다_있다():
    external = _imports() - set(sys.stdlib_module_names) - LOCAL - GPU_INSTALLED
    unknown = external - PACKAGE_OF.keys()
    assert not unknown, f"PACKAGE_OF에 없는 외부 import: {sorted(unknown)}"
    missing = {PACKAGE_OF[m] for m in external} - _required()
    assert not missing, f"requirements에 빠진 패키지: {sorted(missing)}"
