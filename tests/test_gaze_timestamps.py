"""시선 프레임 시각은 헤더 FPS가 아니라 디코더 재생 시각으로 잡는다.

크롬 MediaRecorder webm은 헤더 FPS가 1000으로 읽힌다. frame_idx / fps로 시각을
매기면 26초 영상의 마지막 프레임이 0.78초가 되고 회피 구간이 모두 사라진다.
torch · l2cs가 없는 곳에서도 돌도록 둘 다 가짜 모듈로 막고, VideoCapture도 가짜로 둔다.
"""
import sys
import types

import cv2
import pytest

from infra.gaze_analysis.gaze_metrics import compute_gaze_metrics


class FakeCapture:
    """30fps로 찍힌 영상을 흉내 낸다. 헤더 FPS · 프레임 수는 인자로 준다."""

    def __init__(self, n_frames, header_fps, header_count, pos_supported=True):
        self.n = n_frames
        self.header_fps = header_fps
        self.header_count = header_count
        self.pos_supported = pos_supported
        self.i = -1

    def get(self, prop):
        if prop == cv2.CAP_PROP_FPS:
            return self.header_fps
        if prop == cv2.CAP_PROP_FRAME_COUNT:
            return self.header_count
        if prop == cv2.CAP_PROP_POS_MSEC:
            return self.i * 1000 / 30 if self.pos_supported else 0.0
        return 0.0

    def read(self):
        self.i += 1
        return (self.i < self.n), object()

    def release(self):
        pass


@pytest.fixture
def extract(monkeypatch):
    torch = types.ModuleType("torch")
    torch.device = lambda d: d
    l2cs = types.ModuleType("l2cs")

    class Pipeline:
        def __init__(self, **kwargs):
            self.calls = 0

        def step(self, frame):
            # 10~20초 동안 옆을 본다 (30fps · 6프레임마다 한 번 불림)
            t = self.calls * 6 / 30
            self.calls += 1
            yaw = -0.4 if 10 <= t <= 20 else 0.0
            return types.SimpleNamespace(yaw=[yaw], pitch=[0.0])

    l2cs.Pipeline = Pipeline
    monkeypatch.setitem(sys.modules, "torch", torch)
    monkeypatch.setitem(sys.modules, "l2cs", l2cs)
    monkeypatch.delitem(sys.modules, "infra.gaze_analysis.extract_l2cs_warm", raising=False)
    from infra.gaze_analysis import extract_l2cs_warm as mod

    def run(capture):
        mod._pipeline_cache.clear()
        monkeypatch.setattr(mod.cv2, "VideoCapture", lambda path: capture)
        return mod.extract_l2cs_gaze_warm("x.webm", "w.pkl", device="cpu", frame_skip=6)

    yield run
    # 가짜 torch로 불러온 모듈이 다른 테스트에 남지 않게 지운다
    sys.modules.pop("infra.gaze_analysis.extract_l2cs_warm", None)


def test_browser_webm_header_fps_1000_keeps_real_time(extract):
    # 실측한 크롬 webm: 778프레임 · 헤더 FPS 1000 · 헤더 프레임 수 25,919
    raw = extract(FakeCapture(778, 1000.0, 25919))

    assert raw["frames"][-1]["timestamp"] == pytest.approx(25.8, abs=0.05)
    assert raw["video_duration_sec"] == pytest.approx(778 / 30, abs=0.01)

    m = compute_gaze_metrics(raw["frames"], total_duration_sec=raw["video_duration_sec"])
    assert m.aversion_frequency == 1
    assert m.aversion_events[0].t_start == pytest.approx(10.0, abs=0.3)
    assert m.aversion_events[0].t_end == pytest.approx(20.0, abs=0.3)
    assert m.gaze_maintain_ratio < 0.7


def test_constant_fps_mp4_matches_frame_count_over_fps(extract):
    raw = extract(FakeCapture(900, 30.0, 900))

    assert [f["timestamp"] for f in raw["frames"]] == [round(f["frame_idx"] / 30, 3) for f in raw["frames"]]
    assert raw["video_duration_sec"] == pytest.approx(900 / 30)


def test_falls_back_to_header_fps_when_position_is_not_reported(extract):
    raw = extract(FakeCapture(300, 30.0, 300, pos_supported=False))

    assert raw["frames"][-1]["timestamp"] == pytest.approx(294 / 30, abs=0.001)
    assert raw["video_duration_sec"] == pytest.approx(300 / 30)
