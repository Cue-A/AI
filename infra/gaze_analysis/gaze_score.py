"""
시선 축 점수 변환식. gaze_metrics.py의 GazeMetrics(또는 동등한 dict)를 받아 0~100 점수로 바꾼다.

설계 원칙
    두 요구사항이 서로 부딪힐 수 있다.
    1) 응시유지율 100%에 만점을 주면 안 된다 - 한 곳만 계속 보는 건 부자연스럽다.
    2) 그런데 Q2(실제로 두 번 시선을 뗀 영상)는 Q1(정면 유지)보다 낮게 나와야 한다.

    1번만 강조해서 "100%에 가까울수록 감점"을 세게 걸면, 오히려 회피가 있었던
    Q2 쪽 응시유지율(0.853, 밴드 안)이 Q1(1.0, 밴드 밖)보다 높게 평가되어
    2번이 깨질 수 있다. 그래서:
      - 응시유지율은 "완벽함(1.0)"에 아주 약한 감점만 준다 (거의 표시 나지 않을 정도)
      - 회피빈도는 실제 이탈 횟수에 뚜렷하게 감점한다
    두 답변의 실질적 차이(Q1=회피 0회 vs Q2=회피 2회)가 회피빈도 쪽에서
    분명하게 갈리도록 해서, 전체 순위가 뒤집히지 않게 한다.

    드롭아웃률은 여기서 다루지 않는다. 모델이 얼굴을 못 찾은 건 응답자 잘못이
    아니므로 점수에 안 넣는다 (극단적으로 높으면 이 축 자체를 신뢰 못 하는
    문제인데, 그건 D의 부분 실패 처리에서 다룰 영역이다).
"""


def _ratio_score(gaze_maintain_ratio: float) -> float:
    """
    응시유지율 -> 0~100.

    0.90~0.99 구간을 "정상적으로 잘 유지"로 보고 만점을 준다.
    1.0(단 한 번도 안 흔들림)은 오히려 부자연스러우므로 아주 약하게만 감점한다
    (100 -> 92, 8점 차이 정도로 거의 티 안 나게).
    0.90 밑으로는 실제로 자주 눈을 뗀 것으로 보고 뚜렷하게 감점한다
    (0.40 이하는 0점).
    """
    r = gaze_maintain_ratio

    if 0.90 <= r <= 0.99:
        return 100.0
    if r > 0.99:
        # 0.99~1.0 구간에서 100 -> 92로 아주 약하게만 감점
        return 100.0 - (r - 0.99) / (1.0 - 0.99) * 8.0
    if r < 0.90:
        # 0.90 -> 0.40 구간에서 100 -> 0으로 선형 감점
        floor = 0.40
        if r <= floor:
            return 0.0
        return (r - floor) / (0.90 - floor) * 100.0
    return 100.0


def _aversion_score(aversion_frequency: int) -> float:
    """
    회피빈도 -> 0~100.

    0~1회는 자연스러운 범위로 보고 만점.
    그 이상부터는 실제 이탈 횟수에 비례해 뚜렷하게 감점한다
    (2회당 대략 30점씩, 4회 이상이면 낮은 점수대로 수렴).
    """
    f = aversion_frequency

    if f <= 1:
        return 100.0

    penalty_per_event = 15.0
    score = 100.0 - (f - 1) * penalty_per_event
    return max(0.0, score)


def gaze_score(metrics: dict) -> int:
    """
    metrics: {"gaze_maintain_ratio": float, "aversion_frequency": int, ...}
             (gaze_metrics.py의 GazeMetrics를 dict로 넘기면 됨 - 여분의 키는 무시)

    반환: 0~100 정수, 클수록 좋음.
    가중치는 응시유지율 40% : 회피빈도 60%로, 실제 이탈 여부(회피빈도)가
    전체 순위를 더 많이 좌우하도록 했다. (이 비율은 D의 3축 통합 검증
    단계에서 실제 9개 영상 순위와 비교해 조정될 수 있음)
    """
    ratio_component = _ratio_score(metrics["gaze_maintain_ratio"])
    aversion_component = _aversion_score(metrics["aversion_frequency"])

    combined = 0.4 * ratio_component + 0.6 * aversion_component
    return round(max(0.0, min(100.0, combined)))
