""".env를 읽어 환경변수로 올린다.

docker compose는 .env를 알아서 읽지만, 로컬에서 uvicorn이나 스크립트를 직접
돌리면 아무도 읽지 않는다. 그래서 키를 .env에 넣어 두고도 "키가 없다"는 오류를
보게 된다.

python-dotenv를 쓰지 않는 이유는 더미 이미지에 의존성을 하나라도 덜 넣기
위해서다. 필요한 기능이 스무 줄이면 끝난다.

**이미 설정된 환경변수는 덮어쓰지 않는다.** 배포에서는 컨테이너 환경변수가
진짜 값이고 .env는 로컬 편의용이기 때문이다.
"""
import os
from pathlib import Path

# ai/env.py 기준으로 저장소 루트
DEFAULT_PATH = Path(__file__).resolve().parent.parent / ".env"

# 테스트는 개발자 각자의 .env에 좌우되면 안 된다. 누구 컴퓨터에서는 통과하고
# 누구 컴퓨터에서는 실패하는 테스트가 된다. conftest.py가 이 값을 켠다.
SKIP_ENV = "CUE_SKIP_DOTENV"


def load(path: Path = DEFAULT_PATH) -> list[str]:
    """.env를 읽어 아직 없는 값만 채운다. 채운 키 이름을 준다.

    값은 로그에 남기지 않는다. API 키가 들어 있기 때문이다.
    """
    if os.environ.get(SKIP_ENV):
        return []
    if not path.is_file():
        return []

    loaded = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if not key or not value:
            continue
        if key in os.environ:
            # 셸이나 컨테이너가 이미 준 값이 우선이다
            continue

        os.environ[key] = value
        loaded.append(key)

    return loaded
