"""같은 이력서로 여러 모델을 돌려 주질문 품질과 비용을 나란히 본다.

모델 선택은 추측하지 말고 이걸로 정한다. 한 번 돌리는 데 몇 백 원이면 끝난다.

    python scripts/compare_models.py 이력서.pdf
    python scripts/compare_models.py 이력서.pdf --job "백엔드 개발" --persona pressure
    python scripts/compare_models.py 이력서.pdf --models claude-sonnet-5 claude-opus-5

ANTHROPIC_API_KEY가 필요하다. 실제로 크레딧을 쓴다.
"""
import argparse
import io
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8")

from ai import llm, resume  # noqa: E402
from ai.session_plan import build_plan  # noqa: E402

DEFAULT_MODELS = ["claude-sonnet-5", "claude-opus-5"]
KRW = 1450  # 달러당 원. 대략적인 감만 잡는 용도다


def slots_for(question_count: int, persona: str, seed: int):
    """실제 세션과 같은 방식으로 (카테고리, 난이도)를 뽑는다."""
    plan = build_plan(question_count=question_count, persona=persona, seed=seed)
    slots = [(c, levels[0]) for c, levels in zip(plan["categories"], plan["difficulty"])]
    slots += [(c, "L2") for c in plan["spare_categories"]]
    return plan, slots


def run_one(model: str, resume_file, job_role, persona, slots, effort):
    os.environ["LLM_MODEL"] = model
    os.environ["LLM_EFFORT"] = effort

    started = time.monotonic()
    questions = llm.generate_main_questions(
        resume=resume_file, job_role=job_role, persona=persona, slots=slots
    )
    return questions, time.monotonic() - started


def main() -> int:
    p = argparse.ArgumentParser(description="주질문 생성 모델 비교")
    p.add_argument("resume", help="이력서 파일 (PDF 또는 텍스트)")
    p.add_argument("--job", default="백엔드 개발", help="지원 직무")
    p.add_argument("--persona", default="pressure", choices=["friendly", "pressure"])
    p.add_argument("--count", type=int, default=9, choices=[3, 6, 9], help="문항 수")
    p.add_argument("--seed", type=int, default=1, help="같은 구성으로 비교하려고 고정한다")
    p.add_argument("--effort", default="medium")
    p.add_argument("--models", nargs="+", default=DEFAULT_MODELS)
    args = p.parse_args()

    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ANTHROPIC_API_KEY가 없습니다. .env에 넣거나 export 하세요.")
        return 1

    try:
        resume_file = resume.from_bytes(io.open(args.resume, "rb").read())
    except (OSError, resume.ResumeError) as e:
        print(f"이력서를 읽지 못했습니다: {e}")
        return 1

    plan, slots = slots_for(args.count, args.persona, args.seed)

    print(f"이력서      {args.resume}  ({'PDF' if resume_file.is_pdf else '텍스트'})")
    print(f"직무        {args.job}")
    print(f"페르소나    {args.persona}   문항 수 {args.count}   배분 {plan['shape_name']} {plan['pattern']}")
    print(f"effort      {args.effort}")
    print(f"생성할 주질문 {len(slots)}개 (계획 {len(plan['categories'])} + 예비 {len(plan['spare_categories'])})")

    # 이 스크립트가 쓰는 비용을 로그로 보려면 INFO를 켠다
    import logging
    logging.basicConfig(level=logging.INFO, format="  %(message)s")

    results = {}
    for model in args.models:
        print(f"\n{'=' * 70}\n{model}\n{'=' * 70}")
        try:
            questions, elapsed = run_one(
                model, resume_file, args.job, args.persona, slots, args.effort
            )
        except llm.LlmError as e:
            print(f"  실패: {e}")
            continue

        results[model] = questions
        print(f"  ({elapsed:.1f}초)\n")
        for i, (category, difficulty) in enumerate(slots, start=1):
            mark = "예비" if category in plan["spare_categories"] else "계획"
            print(f"  {i}. [{mark}] {category} {difficulty}")
            print(f"     {questions[category]}\n")

    if len(results) >= 2:
        print(f"{'=' * 70}\n같은 카테고리끼리 비교\n{'=' * 70}")
        for category, difficulty in slots:
            print(f"\n[{category} {difficulty}]")
            for model in results:
                print(f"  {model:<18} {results[model][category]}")

    print(f"\n{'=' * 70}")
    print("위 로그의 '약 $...' 값이 실제 호출 비용입니다.")
    print(f"달러당 {KRW}원으로 환산해 보시면 됩니다.")
    print("질문이 이력서를 제대로 읽었는지, 난이도와 어조가 맞는지 보고 모델을 정하세요.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
