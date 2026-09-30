"""학과 서버에 띄운 AI 서버를 백엔드처럼 불러서 확인한다.

노트북 .env에 두 줄을 넣고 실행한다. 키 값은 출력하지 않는다.

    REMOTE_AI_URL=https://....trycloudflare.com
    REMOTE_AI_SECRET=학과 서버 CUEANDA_SHARED_SECRET과 같은 값

사용법

    python scripts/remote_check.py basic
        /health, 비밀번호 없으면 401인지. 요금 없음

    python scripts/remote_check.py flow --audio URL [URL ...] [--video URL ...]
        세션 시작 → 답변 제출 → 리포트까지 한 바퀴.
        URL은 AI 서버가 받을 수 있는 주소 (학과 서버에서 연 http://localhost:9000/파일 등).
        서버가 llm 모드면 요금이 나가므로 --allow-cost 없이는 멈춘다.

옵션: --persona pressure|friendly  --count 3|6|9
"""
import argparse
import json
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from ai import env  # noqa: E402

env.load()

import os  # noqa: E402

URL = os.environ.get("REMOTE_AI_URL", "").rstrip("/")
SECRET = os.environ.get("REMOTE_AI_SECRET", "")


def call(method, path, body=None, secret=True, headers=None, timeout=30):
    h = {"Content-Type": "application/json"}
    if secret:
        h["X-Cueanda-Secret"] = SECRET
    h.update(headers or {})
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(URL + path, data=data, headers=h, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"{}")
        except ValueError:
            return e.code, {}


def poll(task_id, limit_sec, label):
    start = time.time()
    last = None
    while time.time() - start < limit_sec:
        code, b = call("GET", f"/ai/tasks/{task_id}")
        if code != 200:
            print(f"  [{label}] 폴링 HTTP {code} {b}")
            return None
        if b.get("status") != "processing":
            print(f"  [{label}] {b.get('status')} ({time.time() - start:.1f}초)")
            return b
        stage = (b.get("stage"), b.get("progress"))
        if stage != last:
            print(f"  [{label}] processing {stage[0]}" + (f" {stage[1]}" if stage[1] is not None else ""))
            last = stage
        time.sleep(1)
    print(f"  [{label}] 타임아웃 {limit_sec}초 초과")
    return None


def basic():
    code, b = call("GET", "/health", secret=False)
    print("GET /health:", code, b)
    code, b = call("POST", "/ai/sessions", {}, secret=False)
    print("비밀번호 없이 요청:", code, b.get("error_code"), "(401이면 정상)")
    code, b = call("POST", "/ai/sessions", {}, secret=True)
    print("비밀번호 넣고 빈 요청:", code, b.get("error_code"), "(400 INVALID_REQUEST면 비밀번호 일치)")


def flow(args):
    code, h = call("GET", "/health", secret=False)
    mode = h.get("mode")
    print("서버 모드:", mode)
    if mode != "dummy" and not args.allow_cost:
        sys.exit("llm 모드는 요금이 나갑니다. 허락받은 뒤 --allow-cost를 붙여 다시 실행하세요.")

    body = {"resume_file_url": args.resume, "job_role": "백엔드 개발",
            "persona": args.persona, "question_count": args.count}
    code, s = call("POST", "/ai/sessions", body)
    print("세션 시작:", code, s)
    if code != 202:
        return
    sid = s["session_id"]
    b = poll(s["task_id"], 90, "세션 시작")
    if not b or b["status"] != "done":
        print("  결과:", b)
        return

    rows, i = [], 0
    q = b["result"]
    while q.get("type") != "session_end":
        print(f"\n질문 {q['question_number']}/{q['question_total']} [{q['type']}] {q['text']}")
        print("  audio_url:", q.get("audio_url"))
        audio = args.audio[i % len(args.audio)]
        video = args.video[i % len(args.video)] if args.video else None
        i += 1
        rows.append({"question_id": q["question_id"], "type": q["type"], "text": q["text"],
                     "category": q["category"], "difficulty": q["difficulty"],
                     "question_number": q["question_number"], "audio_url": audio,
                     "video_url": video, "is_timeout": False, "reask_of": q.get("reask_of"),
                     "is_replay": q["is_replay"], "is_spare_topic": q["is_spare_topic"]})
        code, a = call("POST", f"/ai/sessions/{sid}/answers",
                       {"question_id": q["question_id"], "audio_url": audio,
                        "video_url": video, "is_timeout": False})
        if code != 202:
            print("  답변 제출:", code, a)
            return
        b = poll(a["task_id"], 60, "답변 처리")
        if not b or b["status"] != "done":
            print("  결과:", b)
            return
        q = b["result"]
    print("\n세션 종료. 총 질문", q["total_questions"])

    key = f"rpt_{sid}_{uuid.uuid4().hex[:6]}"
    code, r = call("POST", f"/ai/sessions/{sid}/report",
                   {"persona": args.persona, "job_role": "백엔드 개발", "answers": rows},
                   headers={"Idempotency-Key": key})
    print("\n리포트 요청:", code, r)
    if code != 202:
        return
    b = poll(r["task_id"], 600, "리포트")
    if not b or b["status"] != "done":
        print("  결과:", b)
        return
    res = b["result"]
    o = res["overall"]
    print(f"\nreport_status={res['report_status']} 총점 {o['score']}({o['display']}) "
          f"gated={o['gated']} 사용 축 {o['axes_used']} 실패 축 {o['axes_failed']}")
    for name, ax in res["axes"].items():
        print(f"  {name}: {ax['status']} 점수 {ax.get('score')} 근거 {len(ax.get('evidence') or [])}개"
              + (f" metrics {ax.get('metrics')}" if ax.get("metrics") else "")
              + (f" {ax.get('error_code') or ax.get('reason') or ''}"))
    for qs in res["questions"]:
        print(f"  {qs['question_id']} {qs['score']}점 {qs['duration_sec']}초 {qs['word_count']}어절 "
              f"전사: {qs['transcript'][:60]}")
    print("  회복력:", res.get("resilience"))
    print("  기업 코멘트:", res.get("company_comment"))
    print("  개선 답변:", len(res.get("improved_answers") or []), "개")


def main():
    if not URL or not SECRET:
        sys.exit("노트북 .env에 REMOTE_AI_URL, REMOTE_AI_SECRET을 넣어 주세요.")
    p = argparse.ArgumentParser()
    p.add_argument("stage", choices=["basic", "flow"])
    p.add_argument("--audio", nargs="+", default=[])
    p.add_argument("--video", nargs="+", default=[])
    p.add_argument("--resume", default="http://localhost:9000/resume.pdf")
    p.add_argument("--persona", default="pressure", choices=["pressure", "friendly"])
    p.add_argument("--count", type=int, default=3, choices=[3, 6, 9])
    p.add_argument("--allow-cost", action="store_true")
    args = p.parse_args()
    print("대상:", URL)
    if args.stage == "basic":
        basic()
    else:
        if not args.audio:
            sys.exit("--audio 로 답변 음성 주소를 하나 이상 주세요.")
        flow(args)


if __name__ == "__main__":
    main()
