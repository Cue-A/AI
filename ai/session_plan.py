"""세션 구성 플랜 생성 및 진행 제어"""
import random

MAX_PER_TOPIC = 3          # 주질문 1 + 꼬리질문 2
# 1단계 게이트 — 명백히 부실한 답변. LLM을 부르지 않고 바로 되묻는다
HARD_SHORT_SEC, HARD_SHORT_WORDS = 10, 25
MAX_REASK_PER_SESSION = 3            # 되묻기 남발 방지

# 2단계는 LLM 판단. verdict 값
#   "sufficient"   파고들 내용이 있다
#   "insufficient" 부실하다. 되묻는다
#   "escalate"     매우 충실하다. 난이도를 한 단계 올린다

# 면접 흐름상 효과적인 배분만 선별
PATTERNS = {
    3: {
        "점층형": [1, 2],
        "집중형": [2, 1],
    },
    6: {
        "점층형": [1, 2, 3],
        "균형형": [2, 2, 2],
        "집중형": [3, 2, 1],
    },
    9: {
        "점층형":   [1, 2, 3, 3],
        "균형형":   [2, 2, 2, 3],
        "집중형":   [3, 2, 2, 2],
        "중반강화": [2, 3, 2, 2],
        "대비형":   [1, 3, 3, 2],
    },
}

SLOT_POOLS = {
    "도입":   ["지원동기"],
    "역량":   ["프로젝트경험", "직무역량"],
    "인성":   ["협업·갈등", "실패·성장"],
    "마무리": ["미래계획", "가치관·인성"],
}
SLOT_ORDER = ["도입", "역량", "인성", "마무리"]
SPARE_POOL = ["문제해결", "직무역량", "실패·성장", "가치관·인성",
              "프로젝트경험", "협업·갈등", "미래계획"]


def levels_for_topic(persona, topic_idx, topic_total, n):
    if persona == "pressure":
        start = 1 if (topic_idx == 0 and topic_total >= 3) else 2
    else:
        start = 1 if topic_idx < topic_total / 2 else 2
    return [f"L{min(3, start + i)}" for i in range(n)]


def build_plan(question_count, persona, seed=None):
    seed = seed if seed is not None else random.randrange(10**9)
    rng = random.Random(seed)

    shapes = PATTERNS[question_count]
    shape_name = rng.choice(sorted(shapes))
    pattern = list(shapes[shape_name])
    topic_count = len(pattern)

    categories = [rng.choice(SLOT_POOLS[s]) for s in SLOT_ORDER[:topic_count]]
    difficulty = [levels_for_topic(persona, i, topic_count, n)
                  for i, n in enumerate(pattern)]
    if persona == "pressure":
        difficulty[-1][-1] = "L3"

    # 계획에 이미 쓰인 카테고리는 예비에서 제외한다
    spares = [c for c in SPARE_POOL if c not in categories]
    rng.shuffle(spares)

    return {
        "question_count": question_count,
        "shape_name": shape_name,
        "topic_count": topic_count,
        "pattern": pattern,
        "categories": categories,
        "difficulty": difficulty,
        "spare_categories": spares,
        "persona": persona,
        "seed": seed,
    }


def rebuild_plan(previous_plan):
    """재도전 — seed가 같으면 완전히 동일한 구성이 복원된다"""
    return build_plan(previous_plan["question_count"],
                      previous_plan["persona"],
                      seed=previous_plan["seed"])


class SessionRunner:
    """문항 수를 정확히 채우도록 세션 진행을 제어한다.

    짧은 답변 -> 되묻기 1회 -> 여전히 짧으면 꼬리질문 포기하고
    부족분만큼 예비 토픽을 투입한다.
    """

    def __init__(self, plan):
        self.plan = plan
        self.target = plan["question_count"]
        self.asked = 0                 # 실제 발행한 질문 수 (되묻기 제외)
        self.log = []

        self.queue = []                # 남은 토픽 [(카테고리, 난이도리스트)]
        for cat, levels in zip(plan["categories"], plan["difficulty"]):
            self.queue.append([cat, list(levels)])

        self.spares = list(plan["spare_categories"])
        self.cur = None                # 진행 중 토픽
        self.pos = 0                   # 토픽 내 위치
        self.reasked = False           # 이 토픽에서 되묻기를 썼는가
        self.reask_used = 0            # 세션 전체 되묻기 횟수
        self.boost = False             # 다음 꼬리질문 난이도 상향

    # ---------- 내부 ----------
    def _open_topic(self):
        if self.queue:
            self.cur = self.queue.pop(0)
        else:
            cat = self.spares.pop(0) if self.spares else "문제해결"
            self.spares.append(cat)                   # 순환 재사용
            remain = self.target - self.asked
            n = min(MAX_PER_TOPIC, remain)
            persona = self.plan["persona"]
            levels = levels_for_topic(persona, self.plan["topic_count"],
                                      self.plan["topic_count"], n)
            self.cur = [cat, levels]
        self.pos = 0
        self.reasked = False

    def _emit(self, kind, level=None, category=None):
        if kind != "reask":
            self.asked += 1
        item = {"type": kind, "category": category, "difficulty": level,
                "topic": self.plan["topic_count"] if not self.queue else None}
        self.log.append(item)
        return item

    # ---------- 외부 ----------
    def start(self):
        self._open_topic()
        cat, levels = self.cur
        self.pos = 1
        return self._emit("question", levels[0], cat)

    @staticmethod
    def needs_llm(duration_sec, word_count):
        """LLM 판단이 필요한가. False면 명백히 부실하므로 바로 되묻는다"""
        return not (duration_sec < HARD_SHORT_SEC or word_count < HARD_SHORT_WORDS)

    def next(self, duration_sec, word_count, verdict=None, is_timeout=False):
        """직전 답변을 받아 다음 행동을 결정한다.

        duration_sec, word_count : STT 결과에서 계산한 발화 길이
        verdict : LLM 판단. needs_llm()이 False였으면 None
        is_timeout : 제한 시간 만료로 자동 제출된 답변인가
        """
        if self.asked >= self.target:
            return {"type": "session_end", "total": self.asked}

        hard_short = not self.needs_llm(duration_sec, word_count)
        insufficient = hard_short or verdict == "insufficient"
        if is_timeout:
            # 시간이 끊은 답변에 되묻는 것은 오작동이다. 답변이 짧아도 그대로 넘어간다.
            # 되묻기 경로를 아예 타지 않으므로 reask_used도 소모하지 않는다.
            insufficient = False

        cat, levels = self.cur
        has_followup_left = self.pos < len(levels)

        if insufficient:
            if not self.reasked and self.reask_used < MAX_REASK_PER_SESSION:
                self.reasked = True
                self.reask_used += 1
                return self._emit("reask")
            # 되묻기 후에도 부실하면 이 토픽 종료
            self._open_topic()
            cat, levels = self.cur
            self.pos = 1
            return self._emit("question", levels[0], cat)

        if verdict == "escalate":
            self.boost = True

        if has_followup_left:
            level = levels[self.pos]
            if self.boost and level == "L2":
                level = "L3"
            self.boost = False
            self.pos += 1
            return self._emit("followup", level, None)

        self._open_topic()
        cat, levels = self.cur
        self.pos = 1
        return self._emit("question", levels[0], cat)

class RetryRunner:
    """재연습 — 1회차의 토픽 구조를 그대로 재생한다.

    topics: 1회차 진행 기록
        [{"main": {"text","category","difficulty","was_spare"},
          "followups": ["L2","L3"]},   # 1회차에 나간 꼬리질문 난이도
         ...]

    토픽별 꼬리질문 개수가 1회차와 동일하게 고정되므로
    총 문항 수는 자동으로 맞는다.
    """

    def __init__(self, topics, spare_pool=None):
        self.topics = list(topics)
        self.target = sum(1 + len(t["followups"]) for t in self.topics)
        self.asked = 0
        self.log = []

        self.idx = -1
        self.pos = 0
        self.reasked = False
        self.reask_used = 0
        self.boost = False
        self.failed = False        # 이 토픽에서 되묻기 후에도 부실했는가

        used = [t["main"]["category"] for t in self.topics]
        self.spares = spare_pool or [c for c in SPARE_POOL if c not in used]

    def _emit(self, kind, level=None, category=None, text=None,
              spare=False, replay=False):
        if kind != "reask":
            self.asked += 1
        item = {"type": kind, "category": category, "difficulty": level,
                "text": text, "is_spare_topic": spare, "is_replay": replay}
        self.log.append(item)
        return item

    def _spare_question(self):
        """꼬리질문을 낼 수 없을 때 문항 수를 채우는 대체 주질문"""
        if not self.spares:
            self.spares = [t["main"]["category"] for t in self.topics]
        cat = self.spares.pop(0)
        self.spares.append(cat)
        return self._emit("question", "L2", cat, spare=True, replay=False)

    def _open_topic(self):
        self.idx += 1
        self.pos = 0
        self.reasked = False
        self.failed = False
        m = self.topics[self.idx]["main"]
        return self._emit("question", m["difficulty"], m["category"],
                          text=m["text"], spare=m["was_spare"], replay=True)

    def _remaining_in_topic(self):
        return len(self.topics[self.idx]["followups"]) - self.pos

    def start(self):
        return self._open_topic()

    def next(self, duration_sec, word_count, verdict=None, is_timeout=False):
        if self.asked >= self.target:
            return {"type": "session_end", "total": self.asked}

        hard_short = not SessionRunner.needs_llm(duration_sec, word_count)
        insufficient = hard_short or verdict == "insufficient"
        if is_timeout:
            # 시간이 끊은 답변에 되묻는 것은 오작동이다. 답변이 짧아도 그대로 넘어간다.
            # 되묻기 경로를 아예 타지 않으므로 reask_used도 소모하지 않는다.
            insufficient = False

        if insufficient and not self.failed:
            if not self.reasked and self.reask_used < MAX_REASK_PER_SESSION:
                self.reasked = True
                self.reask_used += 1
                return self._emit("reask")
            # 되묻기 후에도 부실하면 이 토픽의 남은 꼬리질문을 포기한다
            self.failed = True

        if self.failed:
            if self._remaining_in_topic() > 0:
                self.pos += 1
                return self._spare_question()      # 문항 수는 대체 질문으로 채운다
            return self._open_topic()

        if verdict == "escalate":
            self.boost = True

        if self._remaining_in_topic() > 0:
            lv = self.topics[self.idx]["followups"][self.pos]
            if self.boost and lv == "L2":
                lv = "L3"
            self.boost = False
            self.pos += 1
            return self._emit("followup", lv)

        return self._open_topic()


def topics_from_log(log, main_texts):
    """1회차 진행 로그를 RetryRunner 입력 형태로 변환한다.

    log        SessionRunner.log
    main_texts 주질문 텍스트 목록 (로그의 question 순서와 일치)
    """
    topics, i = [], 0
    for item in log:
        if item["type"] == "question":
            topics.append({
                "main": {"text": main_texts[i],
                         "category": item["category"],
                         "difficulty": item["difficulty"],
                         "was_spare": item.get("is_spare_topic", False)},
                "followups": [],
            })
            i += 1
        elif item["type"] == "followup" and topics:
            topics[-1]["followups"].append(item["difficulty"])
    return topics
