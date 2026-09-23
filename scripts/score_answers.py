"""질문 · 답변 텍스트로 내용 점수를 뽑는다.

    python scripts/score_answers.py 답변파일.json

답변 파일은 이렇게 생겼다. question이 같으면 생략하고 위에서 물려받는다.

    [
      {"id": "1번 영상", "question": "...", "answer": "..."},
      {"id": "2번 영상", "answer": "..."}
    ]

채점 기준은 docs/내용채점_프롬프트_초안.md를 그대로 읽는다. 서버가 쓰는 것과
같은 채점기라, 여기서 나온 점수가 리포트에 실리는 점수와 같다.

**호출마다 요금이 나간다.** 답변 하나에 약 2원이고, 돌리기 전에 몇 개를
부를지와 예상 비용을 보여주고 물어본다. --yes를 붙이면 묻지 않는다.
"""
import argparse
import json
import os
import sys

# 저장소 어디에서 부르든 ai 패키지를 찾게 한다
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ai import env  # noqa: E402

env.load()

from ai.content_eval import SYSTEM_PROMPT, ContentScore  # noqa: E402
from ai.llm import KRW, PRICING, _client, model  # noqa: E402

KRW_PER_CALL = 2.5  # 실측 기준 어림값. 첫 호출은 캐시를 쓰느라 더 나온다


def load(path: str) -> list[dict]:
    rows = json.load(open(path, encoding="utf-8"))
    question = None
    out = []
    for i, row in enumerate(rows, 1):
        question = row.get("question") or question
        if not question:
            sys.exit(f"{i}번째 항목에 질문이 없습니다")
        out.append({
            "id": str(row.get("id") or i),
            "question": question,
            "answer": row["answer"],
        })
    return out


def score(row: dict) -> tuple[int, str, float]:
    in_rate, out_rate = PRICING[model()]
    r = _client().messages.parse(
        model=model(),
        max_tokens=1000,
        system=[{
            "type": "text",
            "text": SYSTEM_PROMPT,
            "cache_control": {"type": "ephemeral"},
        }],
        messages=[{
            "role": "user",
            "content": f"질문: {row['question']}\n답변: {row['answer']}",
        }],
        output_config={"effort": "low"},
        output_format=ContentScore,
    )
    u = r.usage
    cost = (
        (u.input_tokens or 0) * in_rate
        + (getattr(u, "cache_creation_input_tokens", 0) or 0) * in_rate * 1.25
        + (getattr(u, "cache_read_input_tokens", 0) or 0) * in_rate * 0.1
        + (u.output_tokens or 0) * out_rate
    ) / 1_000_000 * KRW
    p = r.parsed_output
    if p is None:
        raise RuntimeError(f"채점 결과를 해석하지 못했습니다 ({r.stop_reason})")
    return max(0, min(100, int(p.score))), p.reason, cost


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("path", help="질문 · 답변이 담긴 json 파일")
    ap.add_argument("--out", help="결과를 저장할 json 파일")
    ap.add_argument("--yes", action="store_true", help="비용 확인을 건너뛴다")
    args = ap.parse_args()

    rows = load(args.path)
    print(f"답변 {len(rows)}개 · 예상 비용 약 {len(rows) * KRW_PER_CALL:.0f}원")
    if not args.yes and input("돌릴까요? [y/N] ").strip().lower() != "y":
        sys.exit("취소했습니다")

    total = 0.0
    results = []
    for row in rows:
        s, reason, cost = score(row)
        total += cost
        results.append({"id": row["id"], "score": s, "reason": reason})
        print(f"{row['id']:>12s}  {s:>3d}점  {reason}", flush=True)

    print(f"\n총 {len(results)}개 · 약 {total:.0f}원")
    if args.out:
        json.dump(results, open(args.out, "w", encoding="utf-8"),
                  ensure_ascii=False, indent=2)
        print(f"저장: {args.out}")


if __name__ == "__main__":
    main()
