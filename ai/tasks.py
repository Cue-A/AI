"""진짜 백그라운드 실행.

더미는 결과를 즉시 계산할 수 있어 흉내만 내면 됐다 (ai/dummy.py의 PendingTask).
LLM을 붙이면 세션 시작에 이력서 다운로드 + 질문 생성으로 10~30초가 걸리므로,
동기로 처리하면 "요청하면 task_id를 즉시 반환한다"는 계약이 깨진다.

작업은 스레드풀에서 돈다. 전부 IO 대기(HTTP)라 이것으로 충분하다.

**워커가 여러 개여도 된다.** 작업 객체 자체는 그것을 시작한 프로세스에만
있지만, 상태가 바뀔 때마다 보관소(Redis)에 스냅샷을 쓴다. 다른 워커가
폴링하면 그 스냅샷을 읽는다.

작업 객체를 그대로 보관소에 넣지 않는 이유는 threading.Lock을 들고 있어
직렬화가 안 되기 때문이다. 스냅샷은 응답 모델 그대로라 직렬화된다.
"""
import logging
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Optional

from pydantic import BaseModel

from ai.schemas import ErrorCode, TaskErrorResponse, TaskProcessingResponse

logger = logging.getLogger("cue.ai.tasks")

# 전부 IO 대기라 스레드 수를 넉넉히 잡아도 CPU를 쓰지 않는다.
MAX_WORKERS = int(os.environ.get("AI_WORKER_THREADS", "8"))

_executor = ThreadPoolExecutor(max_workers=MAX_WORKERS, thread_name_prefix="cue-ai")


class TaskFailed(Exception):
    """작업이 실패했다. 폴링 결과가 status: error가 된다.

    계약서 8장의 에러 코드를 그대로 실어 나른다.
    """

    def __init__(self, error_code: ErrorCode, message: str):
        super().__init__(message)
        self.error_code = error_code
        self.message = message


class BackgroundTask:
    """백그라운드에서 도는 작업 하나.

    폴링하면 끝나기 전에는 processing을, 끝난 뒤에는 최종 응답을 돌려준다.
    여러 스레드가 동시에 건드리므로 상태 변경은 락으로 감싼다.
    """

    def __init__(self, stages: tuple[str, ...], with_progress: bool = False):
        if not stages:
            raise ValueError("stages는 최소 1개여야 합니다")
        self.stages = stages
        self.with_progress = with_progress

        self._lock = threading.Lock()
        self._stage = stages[0]
        self._final: Optional[BaseModel] = None
        self._completed = threading.Event()

        # 상태를 비춰 둘 보관소. attach로 정한다.
        self._store = None
        self._key: Optional[str] = None

    def attach(self, store, key: str) -> None:
        """상태를 보관소에 비춘다. 워커가 여러 개일 때 폴링이 여기를 읽는다."""
        self._store = store
        self._key = key
        self._publish()

    def _publish(self) -> None:
        """지금 상태를 보관소에 쓴다. 실패해도 작업은 계속한다."""
        if self._store is None or self._key is None:
            return
        try:
            self._store[self._key] = self.poll()
        except Exception:
            # 보관소가 잠깐 안 될 수 있다. 여기서 죽으면 면접이 끊긴다.
            logger.warning("작업 상태를 보관소에 쓰지 못했습니다: %s", self._key)

    # ---------- 작업 쪽에서 부르는 것 ----------
    def set_stage(self, stage: str) -> None:
        """진행 단계를 알린다. 폴링하는 백엔드가 이 값을 본다."""
        with self._lock:
            self._stage = stage
        self._publish()

    def _settle(self, payload: BaseModel) -> None:
        with self._lock:
            self._final = payload
        self._publish()
        self._completed.set()

    # ---------- 폴링 쪽에서 부르는 것 ----------
    def poll(self) -> BaseModel:
        with self._lock:
            if self._final is not None:
                return self._final
            stage = self._stage

        if self.with_progress:
            # 리포트 폴링에만 progress가 있다 (리포트 계약 3장)
            from ai.report_schemas import ReportTaskProcessing

            done = self.stages.index(stage) if stage in self.stages else 0
            return ReportTaskProcessing(
                status="processing",
                stage=stage,
                progress=round((done + 1) / (len(self.stages) + 1), 2),
            )
        return TaskProcessingResponse(status="processing", stage=stage)

    # ---------- 테스트용 ----------
    def wait(self, timeout: float = 30.0) -> BaseModel:
        """작업이 끝날 때까지 기다린다. 테스트에서만 쓴다."""
        if not self._completed.wait(timeout):
            raise TimeoutError(f"{timeout}초 안에 끝나지 않았습니다")
        return self.poll()

    @property
    def finished(self) -> bool:
        return self._completed.is_set()


def _worker(task: BackgroundTask, fn: Callable[[BackgroundTask], BaseModel]) -> None:
    try:
        task._settle(fn(task))
    except TaskFailed as e:
        logger.warning("작업 실패 %s — %s", e.error_code, e.message)
        task._settle(
            TaskErrorResponse(status="error", error_code=e.error_code, message=e.message)
        )
    except Exception:
        # 예상 못 한 오류까지 error로 내보낸다. 폴링이 영원히 processing으로 남으면
        # 백엔드는 타임아웃까지 기다렸다가 원인도 모른 채 실패한다.
        logger.exception("작업이 예상치 못한 오류로 실패했습니다")
        task._settle(
            TaskErrorResponse(
                status="error",
                error_code="LLM_FAILED",
                message="질문 생성 중 오류가 발생했습니다",
            )
        )


def run(
    fn: Callable[[BackgroundTask], BaseModel],
    *,
    stages: tuple[str, ...],
    with_progress: bool = False,
) -> BackgroundTask:
    """fn을 백그라운드에서 돌린다.

    fn은 BackgroundTask를 받아 진행 단계를 알리고 최종 응답 모델을 반환한다.
    실패는 TaskFailed로 던지면 계약서의 에러 코드로 나간다.
    """
    task = BackgroundTask(stages, with_progress)
    _executor.submit(_worker, task, fn)
    return task
