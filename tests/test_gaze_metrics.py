"""답변 시작 직후의 짧은 회피를 세지 않는지 본다."""
from infra.gaze_analysis.gaze_metrics import compute_gaze_metrics


def _frames(*averted: tuple[float, float], total: float = 30.0) -> list[dict]:
    """5fps 프레임. averted 구간에서만 시선이 임계값 아래로 내려간다."""
    out = []
    for i in range(int(total * 5) + 1):
        t = i / 5
        out.append({"frame_idx": i, "timestamp": t, "pitch": 0.0,
                    "yaw": -0.5 if any(s <= t < e for s, e in averted) else 0.0})
    return out


def test_aversion_ending_within_grace_is_ignored():
    m = compute_gaze_metrics(_frames((0.0, 1.4)), total_duration_sec=30.0)
    assert m.aversion_frequency == 0
    assert m.aversion_events == []
    assert m.gaze_maintain_ratio == 1.0


def test_aversion_lasting_past_grace_is_kept():
    m = compute_gaze_metrics(_frames((0.0, 4.0)), total_duration_sec=30.0)
    assert m.aversion_frequency == 1
    assert (m.aversion_events[0].t_start, m.aversion_events[0].t_end) == (0.0, 4.0)


def test_aversion_later_in_answer_is_kept():
    m = compute_gaze_metrics(_frames((0.0, 1.4), (10.0, 20.0)), total_duration_sec=30.0)
    assert m.aversion_frequency == 1
    assert (m.aversion_events[0].t_start, m.aversion_events[0].t_end) == (10.0, 20.0)


def test_short_aversion_merged_into_longer_one_is_kept():
    # 2초 안에 다시 뗀 구간은 합쳐져 유예 뒤까지 이어지므로 진짜 회피로 센다
    m = compute_gaze_metrics(_frames((0.0, 1.0), (2.0, 5.0)), total_duration_sec=30.0)
    assert m.aversion_frequency == 1
    assert m.aversion_events[0].t_end == 5.0
