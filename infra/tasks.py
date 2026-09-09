"""
gpu0(Whisper): 아직 B가 설치 전이라 더미로 대체. 자리만 잡아둠.
gpu1(시선): 이미 검증된 L2CS-Net을 실제로 돌려서, GPU 큐 분리가
            진짜 GPU 작업에서도 문제없이 동작하는지 확인한다.
"""
import time
from celery_app import app


@app.task(name="tasks.transcribe_dummy")
def transcribe_dummy(audio_path: str) -> dict:
    """gpu0 큐 확인용 더미. Whisper 붙으면 이 안을 교체."""
    time.sleep(1)
    return {"audio_path": audio_path, "text": "(더미 전사)", "status": "done", "queue": "gpu0"}


@app.task(name="tasks.analyze_gaze_real")
def analyze_gaze_real(video_path: str, weights_path: str) -> dict:
    """gpu1 큐. 실제 L2CS-Net으로 진짜 추론."""
    from l2cs import Pipeline
    import torch
    import cv2

    p = Pipeline(weights=weights_path, arch="ResNet50", device=torch.device("cuda"))
    cap = cv2.VideoCapture(video_path)
    ok, frame = cap.read()
    cap.release()
    if not ok:
        return {"video_path": video_path, "status": "frame_read_failed", "queue": "gpu1"}

    result = p.step(frame)
    return {
        "video_path": video_path,
        "yaw": float(result.yaw[0]) if len(result.yaw) > 0 else None,
        "pitch": float(result.pitch[0]) if len(result.pitch) > 0 else None,
        "status": "done",
        "queue": "gpu1",
    }
