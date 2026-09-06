# syntax=docker/dockerfile:1
#
# 멀티스테이지
#   base    공통 런타임. python:3.11-slim + requirements-base.txt
#   dummy   더미 서버. LLM · STT · TTS 없음
#   full    실제 모델용. ffmpeg + requirements-full.txt를 추가로 설치
#
#   docker build --target dummy -t cue-ai:dummy .
#   docker build --target full  -t cue-ai:full  .
#
# ffmpeg은 base가 아니라 full에 있다.
# Whisper가 오디오를 디코딩할 때 필요한 것인데 더미는 STT를 돌리지 않아 쓰지 않고,
# 한 패키지가 이미지에 627MB를 더해 더미 배포가 매번 그만큼 무거워지기 때문이다.
#
# dummy 이미지 기준: torch · opencv · whisper가 들어 있으면 안 된다.
# 무거운 라이브러리는 전부 requirements-full.txt로 간다.

# ---------------------------------------------------------------------------
FROM python:3.11-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY requirements-base.txt ./
RUN pip install --no-cache-dir -r requirements-base.txt

# ---------------------------------------------------------------------------
FROM base AS dummy

ENV AI_MODE=dummy

COPY ai/ ./ai/
COPY main.py ./

EXPOSE 8000

# slim 이미지에는 curl이 없으므로 파이썬으로 확인한다.
# start_period가 90초인 것은 full 모드 때문이다. Whisper 로딩에 1분 가까이 걸려서
# 짧게 잡으면 기동 중에 계속 unhealthy로 잡힌다. 이 구간에서는 실패를 세지 않으며,
# 더미는 첫 검사가 통과하는 즉시 healthy가 되므로 손해가 없다.
HEALTHCHECK --interval=30s --timeout=3s --start-period=90s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health')"

# 워커는 반드시 1개다.
# 세션과 태스크를 프로세스 메모리 딕셔너리에 들고 있어서, 워커를 늘리면
# 요청이 다른 워커로 갈 때마다 SESSION_NOT_FOUND가 난다.
# (워커 2개로 같은 task_id를 10회 폴링하면 6회가 404였다.)
# 인스턴스를 늘리려면 Redis 같은 공유 저장소를 먼저 붙여야 한다.
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]

# ---------------------------------------------------------------------------
FROM base AS full

ENV AI_MODE=full

# Whisper가 오디오를 디코딩할 때 필요하다. 더미에는 없다.
RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg \
 && rm -rf /var/lib/apt/lists/*

COPY requirements-full.txt ./
RUN pip install --no-cache-dir -r requirements-full.txt

COPY ai/ ./ai/
COPY main.py ./

EXPOSE 8000

# start_period가 90초인 것은 full 모드 때문이다. Whisper 로딩에 1분 가까이 걸려서
# 짧게 잡으면 기동 중에 계속 unhealthy로 잡힌다. 이 구간에서는 실패를 세지 않으며,
# 더미는 첫 검사가 통과하는 즉시 healthy가 되므로 손해가 없다.
HEALTHCHECK --interval=30s --timeout=3s --start-period=90s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health')"

# 워커는 반드시 1개다.
# 세션과 태스크를 프로세스 메모리 딕셔너리에 들고 있어서, 워커를 늘리면
# 요청이 다른 워커로 갈 때마다 SESSION_NOT_FOUND가 난다.
# (워커 2개로 같은 task_id를 10회 폴링하면 6회가 404였다.)
# 인스턴스를 늘리려면 Redis 같은 공유 저장소를 먼저 붙여야 한다.
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
