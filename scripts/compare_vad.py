"""
VAD 적용 전후 STT 결과 비교 (무음 환각 수정 PR 검증용)

확인하려는 것
  1. 작게 말한 답변이 VAD에 잘리지 않는가  → 볼륨을 줄인 버전에서 어절 수가 유지되는가
  2. 머뭇거림 점수가 크게 달라지지 않는가   → hesitation_score 전후 차이
  3. 무음 환각이 사라졌는가                 → 합성한 무음(약한 잡음) 샘플이 빈 답변이 되는가

사용법 (AI 저장소 루트에서, venv 켠 상태로)
  python scripts/compare_vad.py 답변1.m4a 답변2.webm ...
  python scripts/compare_vad.py --volumes 1.0 0.3 0.1 답변1.m4a

  "수정 전"은 기존 호출(VAD 없음, 세그먼트 필터 없음)을 그대로 재현하고,
  "수정 후"는 지금 ai/stt.py의 transcribe_core()를 그대로 쓴다.
  STT_MODEL · STT_VAD_THRESHOLD 등은 서버와 같은 환경변수(.env)를 따른다.
"""

import argparse
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

try:  # 서버와 같은 설정을 쓰도록 .env를 먼저 읽는다 (stt가 import 시점에 환경변수를 읽음)
    from dotenv import load_dotenv

    load_dotenv(os.path.join(ROOT, ".env"))
except ImportError:
    pass

import numpy as np  # noqa: E402
from faster_whisper import decode_audio  # noqa: E402

from ai import stt  # noqa: E402

SAMPLE_RATE = 16000
WORD_DROP_WARN = 0.2      # 어절 수가 20% 넘게 줄면 경고
HESITATION_DIFF_WARN = 10  # 머뭇거림 점수가 10점 넘게 바뀌면 경고


def transcribe_before(audio):
    """수정 전 호출 그대로 (VAD·세그먼트 필터 없음)."""
    model = stt._get_model()
    segments, info = model.transcribe(audio, language="ko", word_timestamps=True, beam_size=5)
    return list(segments), info.duration


def transcribe_after(audio):
    return stt.transcribe_core(audio)


def metrics(segments, duration):
    sm = stt.compute_speech_metrics(segments)
    em = stt.compute_ending_metrics(sm["text"], segments, duration)
    fm = stt.compute_fluency_metrics(segments, sm["silence_segments"], sm["speech_duration_sec"])
    m = {**sm, **em, **fm, "no_speech": sm["word_count"] == 0}
    m["speech_score"] = stt.speech_score(m)
    return m


def compare(name, audio):
    before = metrics(*transcribe_before(audio))
    after = metrics(*transcribe_after(audio))

    warns = []
    bw, aw = before["word_count"], after["word_count"]
    if bw > 0 and (bw - aw) / bw > WORD_DROP_WARN:
        warns.append(f"어절 {bw}→{aw} 감소 (VAD가 말을 잘랐을 수 있음)")
    dh = after["hesitation_score"] - before["hesitation_score"]
    if aw > 0 and abs(dh) > HESITATION_DIFF_WARN:
        warns.append(f"머뭇거림 {dh:+.1f}점 변화")

    print(f"\n■ {name}")
    print(f"  어절 수        {bw:>5} → {aw:<5}")
    print(f"  침묵 합계(초)  {before['silence_total_sec']:>5} → {after['silence_total_sec']:<5}")
    print(f"  머뭇거림 점수  {before['hesitation_score']:>5} → {after['hesitation_score']:<5}")
    print(f"  말하기 점수    {before['speech_score']:>5} → {after['speech_score']:<5}")
    print(f"  전사(전) {before['text'][:70]!r}")
    print(f"  전사(후) {after['text'][:70]!r}")
    print("  " + ("⚠ " + " / ".join(warns) if warns else "OK"))
    return {"name": name, "warns": warns, "before": before, "after": after}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("files", nargs="*", help="비교할 답변 음성 파일 (m4a, webm, wav, mp3 등)")
    parser.add_argument("--volumes", nargs="+", type=float, default=[1.0, 0.3, 0.1],
                        help="볼륨 배율. 0.3 ≈ -10dB, 0.1 ≈ -20dB (기본: 1.0 0.3 0.1)")
    parser.add_argument("--silence-sec", type=float, default=30.0,
                        help="합성 무음 샘플 길이(초). 0이면 생략")
    args = parser.parse_args()

    print(f"모델 {stt.MODEL_NAME} / VAD {'켬' if stt.STT_VAD else '끔'} "
          f"(threshold {stt.STT_VAD_THRESHOLD}, pad {stt.STT_VAD_SPEECH_PAD_MS}ms)")

    results = []
    if args.silence_sec > 0:
        rng = np.random.default_rng(0)
        noise = rng.normal(0, 0.003, int(SAMPLE_RATE * args.silence_sec)).astype(np.float32)
        r = compare(f"합성 무음 {args.silence_sec:.0f}초 (약한 잡음)", noise)
        if not r["after"]["no_speech"]:
            r["warns"].append("무음인데 전사가 남음")
            print("  ⚠ 무음인데 전사가 남음")
        results.append(r)

    for path in args.files:
        audio = decode_audio(path, sampling_rate=SAMPLE_RATE)
        for vol in args.volumes:
            scaled = np.clip(audio * vol, -1.0, 1.0).astype(np.float32)
            results.append(compare(f"{os.path.basename(path)} × 볼륨 {vol}", scaled))

    bad = [r for r in results if r["warns"]]
    print("\n" + "=" * 60)
    print(f"총 {len(results)}건 중 경고 {len(bad)}건")
    for r in bad:
        print(f"  - {r['name']}: {' / '.join(r['warns'])}")


if __name__ == "__main__":
    main()
