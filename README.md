# Cue AI 서버 — 더미

AI 모의면접 서비스 Cue의 AI 파트 서버입니다.
**주질문은 실제 LLM으로 생성되고, 꼬리질문 · 음성은 아직 고정값입니다.**

백엔드와 프론트가 우리 서버를 기다리지 않고 폴링 · WebSocket push · 로그 저장 ·
재연습 흐름을 검증할 수 있도록 API 껍데기를 먼저 띄운 것입니다.

```
진짜   주질문 (AI_MODE=llm), 문항 수, 난이도 배분, 주제 구조,
       되묻기, 예비 주제, 재연습, 회차 비교
가짜   꼬리질문 · 되묻기 문장, 음성 파일 URL, 발화 길이, 리포트 점수
```

**세션 구성 로직은 실제 서비스와 동일한 코드가 돕니다.** 질문 문장만 고정값일 뿐,
백엔드가 받는 데이터의 구조와 값은 실제와 같습니다.

스펙 원본은 [docs/질문생성_API계약_백엔드전달용.md](docs/질문생성_API계약_백엔드전달용.md)와
[docs/질문 유형.md](docs/질문%20유형.md)입니다.

> **백엔드 담당자분께** — 먼저 읽으실 것 두 개입니다.
> [docs/계약서_변경사항.md](docs/계약서_변경사항.md) 1주차에 계약서에서 바뀐 것. 파싱 코드에 영향이 있습니다
> [docs/백엔드_요청사항.md](docs/백엔드_요청사항.md) S3 · DB 컬럼 등 저희가 요청드리는 것

---

## 빠른 시작

```bash
docker compose up -d --build
curl http://localhost:8000/health
```

```json
{"status":"ok","mode":"dummy"}
```

컨테이너가 뜨면 아래 예시를 그대로 복사해 쓰시면 됩니다.

Docker 없이 돌리려면:

```bash
pip install -r requirements-dev.txt
CUEANDA_SHARED_SECRET=dummy-secret uvicorn main:app --reload --port 8000
```

API 문서는 <http://localhost:8000/docs> 에서 볼 수 있습니다.

---

## 인증

`/health`와 `/ready`를 제외한 **모든 요청**에 공유 시크릿 헤더가 필요합니다.

```
X-Cueanda-Secret: dummy-secret
```

`docker-compose.yml`의 `CUEANDA_SHARED_SECRET` 값과 같아야 합니다.
없거나 다르면 401입니다.

값을 바꾸려면 `.env.example`을 `.env`로 복사해 수정하세요. compose가 자동으로 읽습니다.

```bash
cp .env.example .env
```

```bash
curl -s http://localhost:8000/ai/companies -H 'X-Cueanda-Secret: wrong'
```

```json
{"error_code":"UNAUTHORIZED","message":"시크릿 헤더가 없거나 올바르지 않습니다"}
```

아래 예시는 편의상 변수를 씁니다.

```bash
BASE=http://localhost:8000
H='X-Cueanda-Secret: dummy-secret'
JSON='Content-Type: application/json'
```

---

## 답변을 부실하게 만드는 법 — 먼저 읽어주세요

되묻기 · 예비 주제 경로를 테스트하려면 **부실한 답변**을 보낼 수 있어야 합니다.

실제 서버는 STT 결과에서 발화 길이를 계산하지만 더미에는 STT가 없고,
계약서의 답변 제출 요청에도 길이 정보가 없습니다. 그래서 계약서에 필드를 추가하지 않고
**`audio_url` 문자열**로 받습니다.

| `audio_url` | 판정 | 더미가 쓰는 값 |
|---|---|---|
| `.../ans_1.webm` | 충분한 답변 | 45초 · 60어절 |
| `.../ans_1_short.webm` | **부실한 답변** | 5초 · 10어절 |

`short` 또는 `insufficient`가 들어 있으면 부실로 봅니다. 그 외에는 전부 충분입니다.
실제 STT를 붙일 때 [`ai/dummy.py`](ai/dummy.py)의 `answer_length()` 하나만 교체하면 됩니다.

---

## 엔드포인트

```
POST  /ai/sessions                         세션 시작
POST  /ai/sessions/{session_id}/answers    답변 제출
GET   /ai/tasks/{task_id}                  작업 상태 조회 (폴링)
GET   /ai/companies                        회사 목록
POST  /ai/sessions/{session_id}/abort      세션 중단
GET   /health                              헬스체크 (인증 없음)
GET   /ready                               준비 확인 (인증 없음)

POST  /ai/sessions/{session_id}/report        리포트 생성   ← 아래 별도 절
POST  /ai/sessions/{session_id}/report/retry  실패한 축 재시도
POST  /ai/reports/compare                     회차 비교
```

### 1. 세션 시작

```bash
curl -s -X POST $BASE/ai/sessions -H "$H" -H "$JSON" -d '{
  "resume_file_url": "https://s3.../resume_abc.pdf",
  "job_role": "백엔드 개발",
  "persona": "pressure",
  "company_id": "hyundai_enc",
  "company_profile_override": null,
  "question_count": 6,
  "retry_of_session_id": null,
  "doc_id": null
}'
```

```json
{"session_id":"sess_47d900","task_id":"task_001","question_total":6}
```

```
persona         friendly | pressure          필수
question_count  3 | 6 | 9                    선택, 기본 6
company_id      null 가능 (회사 미선택 연습)
```

첫 질문은 `task_id`로 폴링해서 받습니다.

### 2. 작업 상태 조회 (폴링)

```bash
curl -s $BASE/ai/tasks/task_001 -H "$H"
```

```json
{
  "status": "done",
  "result": {
    "type": "question",
    "question_id": "q_1",
    "reask_of": null,
    "text": "백엔드 개발 직무에 지원하신 이유를 말씀해 주세요.",
    "audio_url": "https://cue-dummy-assets.s3.ap-northeast-2.amazonaws.com/tts/sample.mp3",
    "category": "지원동기",
    "difficulty": "L1",
    "question_number": 1,
    "question_total": 6,
    "topic_index": 1,
    "topic_total": 3,
    "is_spare_topic": false,
    "is_replay": false
  }
}
```

> **기본값에서 더미는 즉시 `status: "done"`을 반환합니다.**
> 실제 서버는 `{"status":"processing","stage":"stt"}`를 여러 번 거칩니다.
> **`DUMMY_POLL_TICKS`를 켜면 지금 그대로 재현할 수 있습니다.** 아래 절을 보세요.

### 3. 답변 제출

```bash
curl -s -X POST $BASE/ai/sessions/sess_47d900/answers -H "$H" -H "$JSON" -d '{
  "question_id": "q_1",
  "audio_url": "https://s3.../ans_1.webm",
  "video_url": "https://s3.../ans_1.mp4",
  "is_timeout": false
}'
```

```json
{"task_id":"task_002"}
```

이 `task_id`를 다시 폴링하면 다음 질문이 나옵니다.

### 4. 되묻기 받아보기

`audio_url`에 `short`를 넣으면 됩니다.

```bash
curl -s -X POST $BASE/ai/sessions/sess_47d900/answers -H "$H" -H "$JSON" -d '{
  "question_id": "q_1",
  "audio_url": "https://s3.../ans_1_short.webm",
  "video_url": null,
  "is_timeout": false
}'
```

폴링 결과:

```json
{
  "status": "done",
  "result": {
    "type": "reask",
    "question_id": "q_1r",
    "reask_of": "q_1",
    "text": "어떤 내용이었는지 조금 더 자세히 말씀해 주시겠어요?",
    "audio_url": "https://cue-dummy-assets.s3.ap-northeast-2.amazonaws.com/tts/sample.mp3",
    "category": null,
    "difficulty": null,
    "question_number": 1,
    "question_total": 6,
    "topic_index": 1,
    "topic_total": 3,
    "is_spare_topic": false,
    "is_replay": false
  }
}
```

**`question_number`가 1 그대로입니다.** 되묻기는 새 질문이 아니라 같은 질문의 재요청이라
문항 수에 세지 않습니다. `category`와 `difficulty`만 null이고 나머지 필드는 값이 옵니다.

### 5. 세션 종료

마지막 문항에 답하면 이렇게 옵니다.

```json
{"status":"done","result":{"type":"session_end","total_questions":6}}
```

`total_questions`는 되묻기를 제외한 실제 질문 수이며 `question_total`과 항상 같습니다.

### 6. 회사 목록

```bash
curl -s $BASE/ai/companies -H "$H"
```

```json
[
  {"company_id":"hyundai_enc","name":"현대건설(주)","industry":"종합건설 · 플랜트"},
  {"company_id":"kb_bank","name":"KB국민은행","industry":"은행 · 금융"}
]
```

`verified`가 false인 회사는 AI 서버에서 걸러서 내보냅니다.
현재 데이터에는 3사가 있고 그중 2사만 나옵니다. 데이터는
[`ai/data/companies.json`](ai/data/companies.json)에 있습니다.

### 7. 세션 중단

```bash
curl -s -X POST $BASE/ai/sessions/sess_47d900/abort -H "$H"
```

```json
{"status":"aborted"}
```

세션 상태를 정리합니다. 같은 세션을 다시 부르면 404입니다.


### 폴링 루프를 검증하려면 — DUMMY_POLL_TICKS

기본값에서 더미는 **항상 즉시 `done`** 을 반환합니다.
그런데 실제 서버는 STT + LLM + TTS가 순차로 돌아 5~15초가 걸리므로
`processing`을 여러 번 거친 뒤 `done`이 됩니다.

즉 기본값으로만 개발하면 **폴링 루프의 `processing` 분기가 한 번도 실행되지 않습니다.**
아래 같은 코드가 더미에서는 100% 통과하고 2주차에 100% 터집니다.

```java
var task = aiClient.getTask(taskId);
var question = task.getResult();   // processing일 때 null → NPE
```

`DUMMY_POLL_TICKS`를 올리면 그 횟수만큼 `processing`을 돌려준 뒤 완료됩니다.

```bash
DUMMY_POLL_TICKS=3 docker compose up -d
```

```
POST /ai/sessions  →  202  task_id=task_001

폴링 1회   status=processing  stage=stt          result 없음
폴링 2회   status=processing  stage=generating   result 없음
폴링 3회   status=processing  stage=tts          result 없음
폴링 4회   status=done        result 있음
```

리포트는 `stage` 값이 다르고 `progress`가 함께 옵니다.

```
폴링 1회   status=processing  stage=transcribing       progress=0.25
폴링 2회   status=processing  stage=analyzing_speech   progress=0.5
폴링 3회   status=processing  stage=analyzing_content  progress=0.75
폴링 4회   status=done        overall=67점
```

```
0 (기본값)   즉시 done. 빠르게 흐름만 볼 때
2~3          폴링 루프 검증. 통합 테스트에 권장
9 이상       타임아웃 처리까지 확인할 때
```

**`processing` 응답에는 `result`가 없습니다.** `status`를 먼저 보고 분기하세요.
`error`도 마찬가지로 `result`가 없습니다.

`done`이 된 뒤에는 몇 번을 더 폴링해도 계속 `done`이라 안전합니다.

---

## 두 가지 모드 — dummy와 llm

`AI_MODE` 하나로 바뀝니다. **이미지는 같습니다.**

```bash
docker compose up -d                    # dummy — 질문이 고정 문장. 요금 0원
AI_MODE=llm docker compose up -d        # llm   — 이력서를 읽고 주질문을 실제로 생성
```

| | dummy | llm |
|---|---|---|
| 주질문 | 카테고리별 고정 문장 | **이력서를 읽고 생성** |
| 꼬리질문 · 되묻기 | 고정 문장 | 고정 문장 (STT가 붙어야 가능) |
| 음성 | 샘플 mp3 | 샘플 mp3 (TTS 연결 전) |
| 세션 구성 | 실제 로직 | 실제 로직 |
| 응답 속도 | 즉시 | 세션 시작에 10~30초 |
| 요금 | 0원 | 세션당 약 50원 |

**백엔드가 받는 응답 형태는 완전히 같습니다.** 필드도 에러 코드도 그대로입니다.
바뀌는 건 `text` 안의 문장과, 세션 시작이 실제로 시간이 걸린다는 점뿐입니다.

### llm 모드에 필요한 것

```bash
cp .env.example .env
```

```
CUEANDA_SHARED_SECRET=실제값      # llm 모드에서는 없으면 기동에 실패합니다
AI_MODE=llm
ANTHROPIC_API_KEY=sk-ant-...     # console.anthropic.com 에서 발급
LLM_MODEL=claude-sonnet-5
LLM_EFFORT=medium
```

`ANTHROPIC_API_KEY`를 발급하실 때 **Settings → Limits에서 월 지출 한도를 함께
걸어두세요.** 실수로 루프를 돌려 크레딧을 태우는 사고를 막아줍니다.

### 세션 시작이 이제 진짜로 오래 걸립니다

llm 모드에서는 이력서 다운로드와 주질문 생성이 **백그라운드에서 실제로 돕니다.**

```
POST /ai/sessions   →  202 즉시   session_id · question_total 은 바로 옵니다
GET  /ai/tasks/{id} →  {"status":"processing","stage":"generating"}
                    →  {"status":"processing","stage":"tts"}
                    →  {"status":"done","result":{...}}
```

**첫 질문이 나오기 전에 답변을 보내면 400 `INVALID_QUESTION_ID`입니다.**
폴링해서 `done`을 받은 뒤에 보내주세요.

```json
{"error_code":"INVALID_QUESTION_ID",
 "message":"첫 질문이 아직 준비되지 않았습니다. task를 폴링해 주세요"}
```

### 실패는 폴링 결과로 나갑니다

```
이력서를 못 받거나 못 읽음   status: error · RESUME_PARSE_FAILED
주질문 생성 실패             status: error · LLM_FAILED
```

이력서는 **PDF · Word(.docx) · 텍스트**를 받습니다. PDF가 결과가 가장 좋습니다
(레이아웃과 표까지 읽힙니다). 한글(.hwp)은 지원하지 않으니 업로드 단계에서
`accept=".pdf,.docx,.txt"`로 막아주세요.

### 비용을 아끼는 장치

```
세션당 호출 1회      계획 토픽과 예비 토픽 주질문을 한 번에 만들어 둡니다
재연습은 호출 0회    1회차 주질문을 텍스트까지 그대로 재생하므로 부를 이유가 없습니다
프롬프트 캐싱        이력서와 시스템 프롬프트에 캐시 지점을 둡니다
```

호출할 때마다 토큰과 대략적인 비용이 로그에 남습니다.

```
주질문 8개 · claude-sonnet-5 · effort=medium — input 7,032 (캐시 읽기 0) / output 1,840 / 약 $0.0325
```

---

## 세션 한 바퀴 — 복사해서 바로 실행

```bash
#!/usr/bin/env bash
set -e
BASE=http://localhost:8000
H='X-Cueanda-Secret: dummy-secret'
JSON='Content-Type: application/json'
BODY=$(mktemp)

# 한글이 든 본문은 파일로 넘긴다. 명령행 인자에 직접 넣으면 셸에 따라 인코딩이 깨진다.
cat > "$BODY" <<'EOF'
{
  "resume_file_url": "https://s3.../resume_abc.pdf",
  "job_role": "백엔드 개발",
  "persona": "pressure",
  "question_count": 9
}
EOF

show() {
  echo "$1" | jq -r '.result | "  \(.type)\t\(.question_id)\tn=\(.question_number)/\(.question_total)\t\(.text)"'
}

# 세션 시작
S=$(curl -s -X POST "$BASE/ai/sessions" -H "$H" -H "$JSON" --data-binary @"$BODY")
SID=$(echo "$S" | jq -r .session_id)
TID=$(echo "$S" | jq -r .task_id)
echo "session $SID · $(echo "$S" | jq -r .question_total)문항"

# 첫 질문
R=$(curl -s "$BASE/ai/tasks/$TID" -H "$H")
show "$R"
QID=$(echo "$R" | jq -r .result.question_id)

# 끝날 때까지 답변 제출 -> 폴링 반복
# 부실한 답변을 보내려면 audio_url에 _short 를 붙인다
for i in $(seq 1 30); do
  echo "{\"question_id\":\"$QID\",\"audio_url\":\"https://s3.../ans_$i.webm\",\"video_url\":null,\"is_timeout\":false}" > "$BODY"
  T=$(curl -s -X POST "$BASE/ai/sessions/$SID/answers" -H "$H" -H "$JSON" --data-binary @"$BODY" | jq -r .task_id)
  R=$(curl -s "$BASE/ai/tasks/$T" -H "$H")
  if [ "$(echo "$R" | jq -r .result.type)" = "session_end" ]; then
    echo "  session_end  total_questions=$(echo "$R" | jq -r .result.total_questions)"
    break
  fi
  show "$R"
  QID=$(echo "$R" | jq -r .result.question_id)
done

rm -f "$BODY"
```

실행 결과 예시 — 9문항 압박형, 전부 충분한 답변:

```
session sess_121e65 · 9문항
  question  q_1  n=1/9  백엔드 개발 직무에 지원하신 이유를 말씀해 주세요.
  question  q_2  n=2/9  가장 자신 있는 기술 스택과 그 이유는 무엇인가요?
  followup  q_3  n=3/9  그 방식은 요청이 몰릴 때 비용이 커지는데, 적절한 선택이었나요?
  question  q_4  n=4/9  실패했던 경험을 말씀해 주세요.
  followup  q_5  n=5/9  그 방식은 요청이 몰릴 때 비용이 커지는데, 적절한 선택이었나요?
  followup  q_6  n=6/9  그 방식은 요청이 몰릴 때 비용이 커지는데, 적절한 선택이었나요?
  question  q_7  n=7/9  개발자로서 가장 중요한 가치는 무엇인가요?
  followup  q_8  n=8/9  그 방식은 요청이 몰릴 때 비용이 커지는데, 적절한 선택이었나요?
  followup  q_9  n=9/9  그 방식은 요청이 몰릴 때 비용이 커지는데, 적절한 선택이었나요?
  session_end  total_questions=9
```

`jq`가 필요합니다 (`brew install jq` / `apt install jq` / `winget install jqlang.jq`).

꼬리질문 문장이 반복되는 것은 정상입니다. 더미는 난이도별 고정 문장을 쓰며,
실제 서버에서는 직전 답변을 읽고 매번 새로 만듭니다.

**배분 패턴은 세션마다 무작위로 정해지므로 카테고리와 토픽 구성은 매번 달라집니다.**
문항 수만 항상 지켜집니다.

> **한글 본문은 파일로 넘기세요.** 위 스크립트가 `--data-binary @` 를 쓰는 이유입니다.
> Windows(PowerShell · Git Bash)에서 `-d '{"job_role":"백엔드 개발"}'` 처럼 명령행 인자에
> 직접 넣으면 인코딩이 깨져 `INVALID_REQUEST`가 납니다. macOS · Linux에서는 둘 다 됩니다.

---

## 재연습

AI 서버는 1회차 로그를 보관하지 않습니다. **재연습의 진실 소스는 백엔드 DB입니다.**
세션 시작 요청에 `replay_log`를 함께 보내주세요.

```bash
curl -s -X POST $BASE/ai/sessions -H "$H" -H "$JSON" -d '{
  "resume_file_url": "https://s3.../resume_abc.pdf",
  "job_role": "백엔드 개발",
  "persona": "friendly",
  "question_count": 6,
  "retry_of_session_id": "sess_abc",
  "replay_log": [
    { "type": "question", "text": "백엔드 개발 직무에 지원하신 이유를 말씀해 주세요.",
      "category": "지원동기", "difficulty": "L1", "is_spare_topic": false },
    { "type": "question", "text": "가장 자신 있는 기술 스택은 무엇인가요?",
      "category": "직무역량", "difficulty": "L1", "is_spare_topic": false },
    { "type": "followup", "difficulty": "L2" },
    { "type": "question", "text": "팀원과 의견이 갈렸던 경험을 말씀해 주세요.",
      "category": "협업·갈등", "difficulty": "L2", "is_spare_topic": false },
    { "type": "followup", "difficulty": "L3" },
    { "type": "followup", "difficulty": "L3" }
  ]
}'
```

```
배열 순서   1회차에 나간 순서 그대로. 순서 자체가 토픽 구조다
question   text · category · difficulty · is_spare_topic 모두 필수
followup   difficulty만 필요
reask      담지 않는다. 재현하지 않는다
```

2회차에서는 **주질문이 텍스트까지 1회차와 동일**하게 나오고 `is_replay: true`가 붙습니다.
꼬리질문은 매번 새로 만들어지므로 `is_replay: false`입니다.

```
retry_of_session_id는 항상 최초 세션을 가리킵니다.
3회차, 4회차도 직전 회차가 아니라 1회차 로그를 보냅니다.
```

**주의 — 옵션을 바꾸면 재연습이 아닙니다.** `question_count`가 원본과 다르면
`replay_log`를 무시하고 일반 세션으로 처리하며, 모든 질문의 `is_replay`가 false가 됩니다.
페르소나 변경은 AI가 알 수 없으니, 페르소나가 바뀌었으면 `replay_log`를 보내지 마세요.

---

## 리포트 생성

면접이 끝나면 답변 전체를 보내 리포트를 받습니다.
스펙 원본은 [docs/리포트생성_API계약_백엔드전달용.md](docs/리포트생성_API계약_백엔드전달용.md)입니다.

```
POST  /ai/sessions/{session_id}/report          리포트 생성 요청
POST  /ai/sessions/{session_id}/report/retry    실패한 축만 재시도
POST  /ai/reports/compare                       회차 비교 · 성장 추이
GET   /ai/tasks/{task_id}                       질문 생성과 같은 엔드포인트로 폴링
```

**AI는 세션도 리포트도 저장하지 않습니다.** 그래서 리포트 요청에 답변 전체를 담아
보내야 하고, 완성된 리포트는 백엔드가 보관합니다. 며칠 뒤에 요청해도 됩니다.

### 리포트 요청

`Idempotency-Key` 헤더가 필수입니다. 같은 키로 다시 요청하면 새 작업을 만들지 않고
기존 `task_id`를 돌려줍니다. 권장 형식은 `rpt_{session_id}_{시도번호}`입니다.

```bash
curl -s -X POST $BASE/ai/sessions/sess_9f2a1c/report \
  -H "$H" -H "$JSON" -H 'Idempotency-Key: rpt_sess9f2a1c_01' -d '{
  "persona": "pressure",
  "job_role": "백엔드 개발",
  "company_id": "hyundai_enc",
  "company_profile_override": null,
  "answers": [
    { "question_id": "q_1", "type": "question",
      "text": "백엔드 개발 직무에 지원하신 이유를 말씀해 주세요.",
      "category": "지원동기", "difficulty": "L1", "question_number": 1,
      "audio_url": "https://s3.../ans_1.webm", "video_url": "https://s3.../ans_1.mp4",
      "is_timeout": false, "reask_of": null,
      "is_replay": false, "is_spare_topic": false },
    { "question_id": "q_1r", "type": "reask",
      "text": "어떤 계기가 있었는지 조금 더 말씀해 주시겠어요?",
      "category": null, "difficulty": null, "question_number": 1,
      "audio_url": "https://s3.../ans_1r.webm", "video_url": null,
      "is_timeout": false, "reask_of": "q_1" }
  ]
}'
```

```json
{"task_id":"task_r001"}
```

> **`is_replay`와 `is_spare_topic`은 계약서 초안에 없던 필드입니다.**
> 응답의 `questions[]`가 두 값을 담고 회차 비교가 `is_replay`로 걸러지는데,
> AI는 세션을 보관하지 않아 요청으로 받지 않으면 알 수 없습니다.
> 로그 테이블에 이미 저장하고 계신 값을 그대로 실어 보내주세요.
> 기본값이 `false`라 두 필드를 빼도 요청은 통과합니다.

### 리포트 폴링

```bash
curl -s $BASE/ai/tasks/task_r001 -H "$H"
```

```json
{
  "status": "done",
  "result": {
    "session_id": "sess_9f2a1c",
    "generated_at": "2026-09-05T09:09:08Z",
    "report_status": "complete",
    "overall": {
      "score": 67, "display": 4, "gated": false, "gate_reason": null,
      "partial": false,
      "axes_used": ["content", "speech", "gaze"], "axes_failed": []
    },
    "axes": {
      "content": {
        "status": "ok", "score": 65, "display": 4, "metrics": {},
        "evidence": [
          { "question_id": "q_1", "t_start": 11.2, "t_end": 17.9,
            "kind": "weakness", "label": "근거 부족",
            "comment": "선택 이유를 설명했으나 비교 대상이 제시되지 않았습니다." }
        ]
      },
      "speech": { "status": "ok", "score": 67, "display": 4, "metrics": {}, "evidence": [] },
      "gaze":   { "status": "ok", "score": 73, "display": 4, "metrics": {}, "evidence": [] }
    },
    "questions": [
      { "question_id": "q_1", "question_number": 1, "category": "지원동기",
        "difficulty": "L1", "is_replay": false, "is_spare_topic": false,
        "score": 76, "display": 4,
        "axes": { "content": 74, "speech": 63, "gaze": 88 },
        "transcript": "(더미 전사) ...", "duration_sec": 90.0, "word_count": 120,
        "was_timeout": false, "had_reask": true }
    ],
    "resilience": { "score": 56, "display": 3, "comment": "..." },
    "company_comment": "...",
    "improved_answers": [ { "question_id": "q_1", "original_excerpt": "...",
                            "suggestion": "...", "t_start": 11.2, "t_end": 18.6 } ]
  }
}
```

읽을 때 주의할 것입니다.

```
metrics          이번 주차에는 빈 객체다. 비어 있는 것이 오류가 아니다
evidence         항상 존재한다. 비어 있을 수 있다
resilience       친절형은 항상 null. 압박 구간이 없어 산출할 수 없다
company_comment  회사를 고르지 않았으면 null
되묻기            독립 문항으로 세지 않는다. 원 질문에 합산되고 had_reask가 true가 된다
```

**더미 점수는 `session_id + question_id`로 정해집니다.** 같은 요청은 항상 같은 점수를
돌려주므로 백엔드가 회귀 테스트를 짤 수 있습니다. 값 자체는 의미가 없습니다.

### 실패·게이트 재현 — URL 문자열 규칙

분석 실패와 적절성 게이트 분기를 테스트할 수 있도록 트리거를 뒀습니다.
답변을 부실하게 만드는 `_short` 규칙과 같은 방식입니다.

| 보낸 값 | 결과 |
|---|---|
| `audio_url: ".../ans_1.webm"` | 정상 |
| `audio_url: ".../ans_1_offtopic.webm"` | 주제 이탈 → **게이트 발동** |
| `audio_url: ".../ans_1_content_fail.webm"` | 내용 분석 실패 → **전체 실패** |
| `audio_url: ".../ans_1_fail.webm"` | 말하기 분석 실패 → **부분 리포트** |
| `video_url: ".../ans_1.mp4"` | 시선 축 `ok` |
| `video_url: null` | 시선 축 `skipped` · `no_video` |
| `video_url: ".../ans_1_fail.mp4"` | 시선 분석 실패 → **부분 리포트** |

### 축마다 실패 처리가 다릅니다

**이 차이가 리포트에서 가장 중요한 부분입니다.**

```
content 실패   전체 실패. 리포트를 만들지 않는다
speech 실패    부분 리포트. 남은 축으로 재정규화
gaze 실패      부분 리포트. 남은 축으로 재정규화
```

`content`만 필수인 이유는 게이트 때문입니다. 내용 관련성이 없으면 적절성 게이트가
작동하지 않아 총점을 신뢰할 수 없습니다. 그래서 `content`가 실패하면 부분 리포트를
만들지 않고 태스크를 `error`로 남깁니다.

```bash
# audio_url에 content_fail이 들어간 요청 → 202 + task_id
curl -s $BASE/ai/tasks/task_r003 -H "$H"
```

```json
{
  "status": "error",
  "error_code": "CONTENT_FAILED",
  "message": "내용 분석에 실패해 리포트를 만들지 못했습니다"
}
```

**`result`가 아예 없습니다.** `report_status`도 `overall`도 오지 않으니,
백엔드는 `status`를 먼저 보고 분기해야 합니다.

`speech`나 `gaze`가 실패했을 때와 비교하면 이렇습니다.

```
                status    report_status   axes_failed   overall
content 실패     error     —               —             —
speech 실패      done      partial         [speech]      63점 (재정규화)
gaze 실패        done      partial         [gaze]        63점 (재정규화)
```

**`failed`와 `skipped`도 다릅니다.** 카메라를 끄고 면접한 경우는 실패가 아니므로
`report_status`가 `complete`로 유지됩니다. 화면에서 전자는 "분석 실패",
후자는 "카메라 미사용"으로 표시하세요.

### 재정규화

축이 빠지면 남은 축에 가중치를 비례 배분합니다.

```
정상          content 0.500  speech 0.300  gaze 0.200
gaze 실패     content 0.625  speech 0.375
speech 실패   content 0.714  gaze 0.286
둘 다 실패    content 1.000
```

### 적절성 게이트

주제에서 벗어난 유창한 답변이 말하기·시선 점수만으로 높은 총점을 받는 것을 막습니다.
**내용 관련성이 30 미만이면 총점에 상한 40을 씌웁니다.**

`audio_url`에 `offtopic`을 넣으면 발동합니다.

```json
{
  "overall": {
    "score": 40,
    "display": 3,
    "gated": true,
    "gate_reason": "content_relevance_low",
    "partial": false,
    "axes_used": ["content", "speech", "gaze"],
    "axes_failed": []
  }
}
```

`gated`가 true이면 프론트는 총점 옆에 안내 문구를 표시합니다. 점수가 낮은 이유가
말하기나 시선이 아니라 **내용**이라는 것을 알려야 합니다.

게이트가 걸려도 **리포트 자체는 정상**입니다 — `report_status`는 `complete`이고
`partial`은 false입니다. 실패가 아니라 상한이 걸린 것뿐입니다.

```
임계값 30 · 상한 40은 잠정값이며 앵커 답변 세트로 튜닝합니다.
값이 바뀌어도 응답 구조는 바뀌지 않습니다.
```

### 실패한 축만 재시도

```bash
curl -s -X POST $BASE/ai/sessions/sess_9f2a1c/report/retry \
  -H "$H" -H "$JSON" -H 'Idempotency-Key: rpt_sess9f2a1c_02' \
  -d '{ "axes": ["gaze"], "answers": [ ... ] }'
```

응답은 요청한 축만 담아 돌려줍니다. 나머지는 `null`이므로 백엔드가 기존 리포트에
병합하면 됩니다. 점수는 최초 리포트와 같은 값이 나옵니다.

```json
{ "status": "done",
  "result": { "session_id": "sess_9f2a1c", "generated_at": "...",
              "axes": { "content": null, "speech": null,
                        "gaze": { "status": "ok", "score": 73 } } } }
```

### 회차 비교

**AI는 리포트를 저장하지 않으므로 백엔드가 전체 회차를 함께 보냅니다.**
회차 수 제한은 없고, 배열은 `round` 오름차순입니다.

```bash
curl -s -X POST $BASE/ai/reports/compare -H "$H" -H "$JSON" -d '{
  "reports": [
    { "session_id": "sess_a", "round": 1, "report": { "...1회차 리포트 전체..." } },
    { "session_id": "sess_b", "round": 2, "report": { "...2회차 리포트 전체..." } }
  ]
}'
```

```json
{
  "latest_round": 2,
  "compared_rounds": [1, 2],
  "vs_previous": { "from_round": 1, "to_round": 2, "overall_delta": -3,
                   "axis_delta": { "content": -5, "speech": 2, "gaze": 0 },
                   "improved": ["q_1"], "declined": ["q_3"], "unchanged": ["q_2"],
                   "comment": "..." },
  "trend": { "overall": [71, 68], "axes": { "content": [70, 65] },
             "comment": "...", "stalled_axes": [], "best_round": 1 },
  "by_question": [ { "question_id": "q_1", "text": "...", "category": "지원동기",
                     "scores": [62, 70], "delta_from_previous": 8,
                     "delta_from_first": 8, "comment": "..." } ],
  "partial_rounds": []
}
```

```
by_question     is_replay가 true인 문항만 담긴다. is_spare_topic은 필터에 쓰지 않는다
partial_rounds  부분 리포트였던 회차. 해당 회차는 trend.overall이 null이 된다
vs_previous     reports가 1개면 null. 회차가 하나뿐일 때도 오류가 아니다
stalled_axes    최근 3회차 변화가 임계 미만인 축. 없으면 빈 배열
```

**부분 리포트 회차는 총점 비교에서 빠집니다.** 재정규화된 총점은 다른 회차와
스케일이 달라서입니다. `trend.overall`에 `null`이 들어가므로 그래프에서 끊거나
점선으로 표시하세요.

### 리포트 에러

```
400  INVALID_REQUEST     Idempotency-Key 누락, 재시도 축 미지정
400  INVALID_ANSWERS     answers 배열이 비었거나 형식 오류
422  REPORT_TOO_SHORT    채점할 답변이 2문항 미만. 되묻기는 문항으로 세지 않는다
```

폴링 결과로 나가는 코드입니다 (HTTP 오류가 아니라 `status: "error"`).

```
CONTENT_FAILED   내용 분석 실패. 전체 실패   audio_url에 content_fail로 재현 가능
```

`STT_FAILED` · `MEDIA_FETCH_FAILED`는 실제 분석이 붙는 3~4주차부터 나옵니다.
더미에서는 발생시킬 수 없습니다.

---

## 에러

모든 HTTP 에러는 같은 형식입니다.

```json
{"error_code": "SESSION_ENDED", "message": "이미 종료된 세션입니다"}
```

| 코드 | HTTP | 언제 | 재시도 |
|---|---|---|---|
| `INVALID_REQUEST` | 400 | 일반 검증 오류 | 없음 |
| `INVALID_CATEGORY` | 400 | `replay_log`의 카테고리 문자열 불일치 | 없음 |
| `INVALID_QUESTION_ID` | 400 | 현재 질문과 불일치 | 없음 |
| `UNAUTHORIZED` | 401 | 시크릿 헤더 누락 또는 불일치 | 없음 |
| `SESSION_NOT_FOUND` | 404 | 세션 없음 또는 만료 | 없음. 세션 aborted 처리 |
| `SESSION_ENDED` | 409 | 이미 종료된 세션에 답변 제출 | 없음. 무시 (중복 제출) |
| `INVALID_ANSWERS` | 400 | 리포트 `answers` 배열 형식 오류 | 없음 |
| `RESUME_PARSE_FAILED` | 422 | 이력서 파싱 실패 | 없음 |
| `REPORT_TOO_SHORT` | 422 | 채점할 답변이 2문항 미만 | 없음. 사용자 안내 |
| `STT_FAILED` | 500 | 음성 인식 실패 | 1회, 성공률 낮음 |
| `LLM_FAILED` | 500 | 질문 생성 실패 | 1회 |
| `TTS_FAILED` | 500 | 음성 합성 실패 | 없음. `audio_url` null로 진행 |

> **더미가 실제로 내는 코드는 위 표에서 400 · 401 · 404 · 409 다섯 개뿐입니다.**
> `RESUME_PARSE_FAILED` · `STT_FAILED` · `LLM_FAILED` · `TTS_FAILED`는 이력서 파싱 ·
> 음성 인식 · 질문 생성 · 음성 합성이 붙는 2~3주차부터 나옵니다.
> 지금은 발생시킬 수 없지만 분기 코드는 미리 짜두셔도 됩니다.

**카테고리는 가운뎃점(·)까지 정확히 일치해야 합니다.**

```
지원동기  직무역량  프로젝트경험  문제해결
협업·갈등  실패·성장  가치관·인성  미래계획
```

`협업`처럼 보내면 `INVALID_CATEGORY`입니다.

```bash
curl -s -X POST $BASE/ai/sessions -H "$H" -H "$JSON" -d '{
  "resume_file_url":"u","job_role":"백엔드 개발","persona":"friendly",
  "question_count":6,"retry_of_session_id":"sess_abc",
  "replay_log":[{"type":"question","text":"t","category":"협업",
                 "difficulty":"L1","is_spare_topic":false}]
}'
```

```json
{"error_code":"INVALID_CATEGORY","message":"replay_log의 category가 카테고리 8종과 일치하지 않습니다. 가운뎃점(·)까지 정확히 같아야 합니다."}
```

---

## 백엔드가 알아둘 것

**1. 진행률은 `question_number / question_total`로 표시합니다.**
문항 수는 항상 지켜지지만 토픽 수는 세션마다 다릅니다.
`topic_index`와 `topic_total`은 참고용이며, 예비 주제가 투입되면 `topic_total`이
세션 도중에 늘어납니다.

**2. 되묻기는 문항 수에 포함되지 않습니다.**
`question_number`가 올라가지 않으므로 진행률이 뒤로 가거나 멈춘 것처럼 보이지 않습니다.
로그 테이블의 `category`, `difficulty` 컬럼은 NULL을 허용해야 합니다.

**3. `reask_of`를 반드시 저장하세요.**
되묻기 답변은 원 질문의 답변에 이어 붙여 하나로 채점합니다.
`q_4r` 형태의 접미사로 추측하지 말고 값으로 저장하세요.

**4. 회차 비교는 `is_replay`가 true인 질문만 대상입니다.**
`is_spare_topic`으로 거르면 안 됩니다. 1회차 예비 토픽 주질문은
`is_spare_topic: true`이면서 `is_replay: true`라 비교 대상인데도 잘못 빠집니다.

**5. 세션 상태는 메모리에만 있습니다.**
서버를 재배포하면 진행 중이던 세션이 사라져 `SESSION_NOT_FOUND`가 납니다. 버그가 아닙니다.
개발 기간에는 재배포 전에 공유하겠습니다.

**6. `audio_url`은 지금 샘플 mp3 하나를 계속 반환합니다.**
실제 S3 URL이 아니므로 재생 테스트에는 쓸 수 없습니다.
TTS가 붙기 전까지는 URL 형태와 null 처리 분기만 확인해 주세요.

---

## 계약서와 다르거나 계약서에 없던 것

더미를 만들면서 정한 것들입니다. **계약서 8장에 반영이 필요합니다.**

| 항목 | 결정 |
|---|---|
| HTTP 에러 본문 | `{error_code, message}`. 폴링 실패 응답과 같되 `status`는 뺌 |
| `INVALID_REQUEST` | 계약서에 없던 코드. 일반 검증 오류를 422가 아닌 400으로 통일하려고 추가 |
| 검증 오류 HTTP 코드 | FastAPI 기본 422가 아니라 **전부 400** |
| `retry_of_session_id`만 있고 `replay_log` 없음 | 400 `INVALID_REQUEST` |
| `GET /ai/tasks/{없는 id}` | 404 `SESSION_NOT_FOUND` (작업 전용 코드가 계약서에 없음) |
| 존재하지 않는 경로 | 404 `INVALID_REQUEST`. `SESSION_NOT_FOUND`를 주면 원인을 잘못 짚게 됨 |
| `topic_total` | `max(계획된 주제 수, 지금까지 열린 주제 수)` |
| 부실 답변 판정 | `audio_url` 문자열 규칙 (위 참고). 더미 전용이며 계약서 필드 추가 없음 |
| `/health`, `/ready` | 계약서 엔드포인트 목록에 없으나 배포용으로 추가. 인증 제외 |

리포트 생성 계약에서 정한 것입니다.

| 항목 | 결정 |
|---|---|
| `answers[].is_replay` · `is_spare_topic` | **요청 스키마에 추가.** 응답 `questions[]`가 두 값을 담고 회차 비교가 `is_replay`로 걸러지는데, AI가 세션을 보관하지 않아 입력으로 받아야 함. 기본값 `false` |
| `AxisResult`의 `error_code` · `reason` | 해당 상태일 때만 내보냄. `ok` 축에는 두 필드가 아예 없음 |
| 부분 실패 트리거 | `video_url` · `audio_url`에 `fail`이 있으면 해당 축 `failed`. 더미 전용 |
| 더미 점수 | `session_id + question_id` 해시로 50~90. 같은 요청은 항상 같은 점수 |
| 게이트 재현 | `audio_url`에 `offtopic` → 내용 점수 10~29로 떨어져 `gated: true`. 임계 30 · 상한 40 |
| `content` 축 실패 | `audio_url`에 `content_fail` → 태스크가 `status: error` · `CONTENT_FAILED`. 리포트 본문 없음 |

`ai/session_plan.py`의 `SessionRunner.next` / `RetryRunner.next`에
`is_timeout=False` 인자를 추가했습니다. 기본값이 False라 기존 동작은 그대로입니다.
2880개 조합으로 전후 동일성을 확인했습니다.

---

## 배포 전에 반드시 확인할 것

더미 서버라도 실제로 띄워서 백엔드가 붙습니다. 아래는 "더미라서 괜찮은 것"이 아니라
**틀리면 그냥 사고가 나는 것들**입니다.

```
[ ] 워커는 1개                     uvicorn --workers 1
[ ] 인스턴스 · 레플리카도 1개       오토스케일링 끄기
[ ] CUEANDA_SHARED_SECRET 설정      실제 값으로
[ ] 재배포 일정을 백엔드와 공유      진행 중이던 세션이 사라집니다
[ ] 폴링 타임아웃                   세션 시작 90초 / 답변 60초 / 리포트 10분
[ ] audio_url · video_url 규칙은 운영 코드에 넣지 말 것
```

### 1. 워커와 인스턴스는 반드시 1개

**세션과 태스크를 프로세스 메모리 딕셔너리에 들고 있습니다.**
워커나 인스턴스를 늘리면 요청이 다른 프로세스로 갈 때마다 세션을 찾지 못합니다.

실제로 워커 2개로 띄우고 같은 `task_id`를 10번 폴링한 결과입니다.

```
[200, 200, 404, 404, 200, 404, 200, 404, 404, 404]
  200 4회 / 404 6회
```

`Dockerfile`에 `--workers 1`을 박아뒀지만, 아래처럼 하면 그대로 깨집니다.

```
gunicorn -w 4                     ✗
ECS · K8s 레플리카 2개 이상          ✗
오토스케일링                        ✗
로드밸런서 뒤에 인스턴스 여러 대       ✗
```

**인스턴스를 늘리려면 Redis 같은 공유 저장소를 먼저 붙여야 합니다.**
지금은 인프라 담당자가 따로 맡기로 되어 있어 붙이지 않았습니다.

### 2. 시크릿

`CUEANDA_SHARED_SECRET`을 설정하지 않으면 더미 모드에서는 기본값 `dummy-secret`으로
뜨고 경고 로그를 남깁니다. **`AI_MODE`가 `dummy`가 아니면 아예 기동에 실패합니다.**

```
$ docker run -e AI_MODE=full cue-ai:dummy
RuntimeError: CUEANDA_SHARED_SECRET가 설정되지 않았습니다.
              AI_MODE=full에서는 기본값 'dummy-secret'을 쓸 수 없습니다.
```

기동 로그에 아래 경고가 보이면 시크릿이 안 들어간 것입니다.

```
CUEANDA_SHARED_SECRET가 설정되지 않아 기본값 'dummy-secret'을 사용합니다.
```

### 3. 세션은 임시 보관입니다

```
세션    최대 500개    넘으면 오래된 것부터 정리
태스크  최대 5000개   넘으면 오래된 것부터 정리
```

상한을 넘거나 서버를 재배포하면 진행 중이던 세션이 사라져 `SESSION_NOT_FOUND`가
납니다. **버그가 아닙니다.** 백엔드는 이 코드를 받으면 재시도하지 말고 세션을
`aborted`로 처리하면 됩니다. 사용자에게 보이는 상태는 백엔드 DB가 기준입니다.

개발 기간에는 재배포 전에 공유하겠습니다.

### 4. 더미 전용 규칙은 운영 코드에 넣지 마세요

아래는 **더미에서만 동작하는 테스트용 장치**입니다. 실제 모델이 붙으면 전부 사라집니다.

```
audio_url에 _short         부실한 답변 → 되묻기
audio_url에 _offtopic      주제 이탈 → 적절성 게이트
audio_url에 _content_fail  내용 분석 실패 → 전체 실패
audio_url에 _fail          말하기 분석 실패 → 부분 리포트
video_url에 _fail          시선 분석 실패 → 부분 리포트
```

테스트 코드나 개발용 픽스처에만 쓰시고, 운영 경로에서 URL을 조작하지 마세요.

### 5. 폴링 루프는 DUMMY_POLL_TICKS로 검증하세요

기본값에서는 즉시 `done`이 되므로 `processing` 분기가 실행되지 않습니다.
**통합 테스트는 `DUMMY_POLL_TICKS=3`으로 돌려주세요.**

```bash
DUMMY_POLL_TICKS=3 docker compose up -d
```

폴링 루프에 아래가 들어가야 합니다.

```
processing이 여러 번 올 수 있다           한 번만 호출하고 끝내면 안 됩니다
processing · error에는 result가 없다      status를 먼저 보고 분기
stage 값은 프론트에 그대로 노출하지 않는다   백엔드 enum으로 매핑
타임아웃                                 세션 시작 90초 / 답변 60초 / 리포트 10분
```

### 6. 아직 없는 것

```
LLM · STT · TTS 연결       2~3주차
S3 업로드                  3주차. 지금 audio_url은 샘플 mp3 하나 고정
이력서 파싱                resume_file_url을 받기만 하고 쓰지 않습니다
RESUME_PARSE_FAILED        위와 같은 이유로 재현 불가
STT_FAILED · MEDIA_FETCH_FAILED   실제 분석이 붙어야 발생
```

**스키마는 확정되어 있으므로 백엔드 파싱 코드는 지금 만들어도 됩니다.**
이후에는 필드 추가만 발생하며 기존 필드명과 타입은 바뀌지 않습니다.

---

## 개발

### 테스트

```bash
pip install -r requirements-dev.txt
python -m pytest -q
```

```
2258 passed in 40s
```

| 파일 | 내용 |
|---|---|
| `tests/test_schemas.py` | 계약서의 JSON 예시가 그대로 파싱되는지 |
| `tests/test_session_plan.py` | `is_timeout` 인자 회귀 (문항 수 · 되묻기 한도) |
| `tests/test_scenarios.py` | 문서 13장 시나리오 A·B·C·D + 재연습 + 인증 |
| `tests/test_report.py` | 리포트 생성 · 부분 실패 · 멱등성 · 회차 비교 |
| `tests/test_ops.py` | 운영 제약 — 워커 수 · 시크릿 가드 · 보관소 상한 |
| `tests/test_polling.py` | processing 흉내 — 단계 진행 · progress · result 부재 |
| `tests/test_llm.py` | 이력서 로딩(PDF · Word) · 주질문 생성 요청 · 실패 처리 |
| `tests/test_tasks.py` | 백그라운드 실행 — 진행 단계 · 실패 · 동시성 |
| `tests/test_pipeline.py` | 세션 시작 흐름 — 더미/llm 분기 · 인재상 · 실패 |

시나리오 테스트는 FastAPI TestClient로 실제 HTTP 요청을 보냅니다.

### Docker

```bash
docker build --target dummy -t cue-ai:dummy .   # 319MB
docker build --target full  -t cue-ai:full  .   # 946MB
```

```
base    python:3.11-slim + requirements-base.txt
dummy   base 상속. AI_MODE=dummy
full    base 상속. ffmpeg + requirements-full.txt
```

`ffmpeg`은 `base`가 아니라 `full`에 있습니다. Whisper용인데 더미는 STT를 돌리지 않고,
한 패키지가 이미지에 627MB를 더하기 때문입니다.
**dummy 이미지에는 torch · opencv · whisper가 들어 있지 않습니다.**

`HEALTHCHECK`의 `start_period`는 90초입니다. full 모드에서 Whisper 로딩에 1분 가까이
걸려서 짧게 잡으면 기동 중에 계속 unhealthy로 잡힙니다.
`docker-compose.yml`의 `depends_on`에 `condition: service_healthy`로 쓰실 수 있습니다.

### 구조

```
main.py                FastAPI 진입점, 예외 핸들러, /health · /ready
ai/
  router.py            /ai/* 엔드포인트, 시크릿 헤더 검증
  schemas.py           질문 생성 계약의 요청 · 응답 모델
  dummy.py             고정 질문 텍스트, 세션 진행, 메모리 보관소
  llm.py               Claude로 주질문 생성
  resume.py            이력서 다운로드 (PDF · Word · 텍스트)
  tasks.py             백그라운드 실행
  pipeline.py          세션 시작 흐름 — 더미/llm 분기
  report_router.py     리포트 엔드포인트
  report_schemas.py    리포트 생성 계약의 요청 · 응답 모델
  report_dummy.py      점수 생성, 축 재정규화, 회차 비교
  errors.py            에러 응답 형식
  companies.py         회사 목록 (verified 필터)
  session_plan.py      세션 구성 로직 — 수정 금지
  data/companies.json
```

### 다음 주차

```
1주차   더미 응답 (스키마 확정, 흐름 검증)     ← 지금
2주차   LLM 연결 (실제 질문 생성)
3주차   Whisper + TTS 연결 (실제 음성)
4주차   더미를 실제 모듈로 교체
```

**스키마는 이번 주차에 확정하고 이후 추가만 허용합니다.**
기존 필드명과 타입은 바꾸지 않습니다.
