# 리포트 생성 — AI ↔ 백엔드 계약

| 항목 | 값 |
| --- | --- |
| 버전 | v0.2 |
| 짝 문서 | 「질문 생성 API 계약」 |
| 확정 범위 | 응답 구조 · 에러 · 부분실패 · 멱등성 · 타임스탬프 규칙 |
| v0.2 변경 | `answers[]`에 `is_replay` · `is_spare_topic` 추가, 에러 본문 형식 명시 |
| 미확정 | 축별 세부 지표 필드, 가중치, 임계값 |

미확정 항목은 **필드 추가만** 발생한다.
구조와 기존 필드명은 바뀌지 않으므로 백엔드는 지금 파싱 코드를 짜도 된다.

---

## 0. 원칙

**점수 축은 3개다.**

```
content   내용    관련성 · 구체성 · 논리성
speech    말하기  속도 · 필러 · 침묵 · 마무리
gaze      시선    응시 유지 · 회피 빈도
```

마무리는 독립 축이 아니라 `speech`의 하위 지표다.
리포트 화면에는 별도 항목으로 표시할 수 있지만 점수 구조상으로는 4축이 아니다.

**내부는 0~100, 표시는 1~5다.**

```
0~19 → 1     20~39 → 2     40~59 → 3     60~79 → 4     80~100 → 5
```

AI가 두 값을 모두 내려주므로 백엔드가 변환하지 않는다.

**모든 감점 근거에 타임스탬프가 붙는다.**

근거 표시가 P1이더라도 필드는 처음부터 넣는다.
나중에 추가하면 이미 생성된 리포트를 재생성해야 한다.

---

## 0-1. 인증

질문 생성 계약과 동일하다. 모든 요청에 공유 시크릿 헤더를 넣는다.

```
X-Cueanda-Secret: <SHARED_SECRET>
```

헤더가 없거나 값이 다르면 401(`UNAUTHORIZED`)을 반환한다.

---

## 1. 엔드포인트

```
POST  /ai/sessions/{session_id}/report          리포트 생성 요청
GET   /ai/tasks/{task_id}                       진행 상황 (질문 계약과 동일)
POST  /ai/sessions/{session_id}/report/retry    실패한 축만 재시도
POST  /ai/reports/compare                       회차 비교 · 성장 추이
```

리포트 본문은 AI가 저장하지 않는다. **완성된 리포트의 보관은 백엔드가 한다.**
재조회 엔드포인트를 두지 않는 이유이며, 질문 로그와 같은 원칙이다.

---

## 2. 리포트 생성 요청

```
POST /ai/sessions/{session_id}/report
Idempotency-Key: rpt_sess9f2a1c_01
```

```json
{
  "persona": "pressure",
  "job_role": "백엔드 개발",
  "company_id": "hyundai_enc",
  "company_profile_override": null,
  "answers": [
    {
      "question_id": "q_1",
      "type": "question",
      "text": "백엔드 개발 직무에 지원하신 이유를 말씀해 주세요.",
      "category": "지원동기",
      "difficulty": "L1",
      "question_number": 1,
      "audio_url": "https://s3.../ans_1.webm",
      "video_url": "https://s3.../ans_1.mp4",
      "is_timeout": false,
      "reask_of": null,
      "is_replay": false,
      "is_spare_topic": false
    },
    {
      "question_id": "q_1r",
      "type": "reask",
      "text": "어떤 계기가 있었는지 조금 더 말씀해 주시겠어요?",
      "category": null,
      "difficulty": null,
      "question_number": 1,
      "audio_url": "https://s3.../ans_1r.webm",
      "video_url": "https://s3.../ans_1r.mp4",
      "is_timeout": false,
      "reask_of": "q_1"
    }
  ]
}
```

| 필드 | 설명 |
| --- | --- |
| `answers` | 세션에서 실제로 나간 질문과 답변 전체. 순서대로 |
| `video_url` | 시선 분석용. null이면 시선 축을 건너뛴다 |
| `is_timeout` | 제한 시간 만료로 잘린 답변인가 |
| `reask_of` | 되묻기인 경우 원 질문의 `question_id` |
| `category` | 카테고리 8종 문자열과 정확히 일치해야 한다 |
| `is_replay` | 1회차와 동일한 주질문인가. 없으면 `false`로 본다 |
| `is_spare_topic` | 예비 토픽에서 나온 질문인가. 없으면 `false`로 본다 |

**`is_replay`와 `is_spare_topic`은 v0.2에서 추가됐다.**

응답 `questions[]`가 두 값을 담고 8장의 회차 비교가 `is_replay`로 걸러지는데,
AI 서버는 세션을 보관하지 않아 요청으로 받지 않으면 알 수 없다.
백엔드 로그 테이블에 이미 저장하는 값이므로 그대로 실어 보내면 된다.

기본값이 `false`이므로 두 필드가 없는 요청도 그대로 파싱된다.
다만 **보내지 않으면 회차 비교의 `by_question`이 항상 빈 배열이 된다.**

카테고리 8종은 질문 생성 계약과 동일하다. 가운뎃점(·)까지 포함한다.

```
지원동기  직무역량  프로젝트경험  문제해결
협업·갈등  실패·성장  가치관·인성  미래계획
```

페르소나 값도 동일하게 `friendly` / `pressure`를 쓰며,
문서에서는 친절형 / 압박형으로 부른다.
| `company_profile_override` | 미등록 기업 인재상 직접 입력값. 없으면 null |

**`is_timeout`이 true인 답변은 감점하지 않는다.**
시간이 끊은 것이지 답변자가 마무리를 못 한 것이 아니다.
마무리 지표에서 제외하고, 리포트에 "시간 초과로 중단됨"으로 표시한다.

**되묻기 답변은 원 질문에 합산한다.**
`reask_of`가 가리키는 질문의 답변에 이어 붙여 하나로 채점한다.
되묻기 자체는 독립 문항으로 점수를 내지 않는다.

### 응답 (즉시)

```json
202 Accepted
{ "task_id": "task_r001" }
```

### 멱등성

`Idempotency-Key` 헤더를 필수로 받는다.
같은 키로 다시 요청하면 새 작업을 만들지 않고 기존 `task_id`를 반환한다.

권장 형식은 `rpt_{session_id}_{시도번호}`다.
사용자가 재분석을 누르면 시도번호를 올린다.

---

## 3. 진행 상황

```
GET /ai/tasks/{task_id}
```

```json
{ "status": "processing", "stage": "transcribing", "progress": 0.4 }
```

`stage` 값

```
transcribing   음성 인식
analyzing_speech
analyzing_gaze
analyzing_content
composing      리포트 조립
```

질문 계약과 마찬가지로 **이 값을 프론트에 그대로 노출하지 않는다.**
백엔드에서 자체 enum으로 매핑한다.

**폴링 타임아웃은 10분을 권장한다.**
영상 다운로드와 CV 처리가 포함되어 질문 생성보다 훨씬 오래 걸린다.

---

## 4. 리포트 응답

```json
{
  "status": "done",
  "result": {
    "session_id": "sess_9f2a1c",
    "generated_at": "2026-09-05T14:22:31Z",
    "report_status": "partial",

    "overall": {
      "score": 68,
      "display": 4,
      "gated": false,
      "gate_reason": null,
      "partial": true,
      "axes_used": ["content", "speech"],
      "axes_failed": ["gaze"]
    },

    "axes": {
      "content": {
        "status": "ok",
        "score": 72,
        "display": 4,
        "metrics": {},
        "evidence": [
          {
            "question_id": "q_3",
            "t_start": 12.4,
            "t_end": 19.8,
            "kind": "weakness",
            "label": "근거 부족",
            "comment": "선택 이유를 설명했으나 비교 대상이 제시되지 않았습니다."
          }
        ]
      },
      "speech": {
        "status": "ok",
        "score": 61,
        "display": 4,
        "metrics": {},
        "evidence": []
      },
      "gaze": {
        "status": "failed",
        "error_code": "GAZE_FAILED",
        "score": null,
        "display": null,
        "metrics": null,
        "evidence": []
      }
    },

    "questions": [
      {
        "question_id": "q_1",
        "question_number": 1,
        "category": "지원동기",
        "difficulty": "L1",
        "is_replay": false,
        "is_spare_topic": false,
        "score": 70,
        "display": 4,
        "axes": { "content": 74, "speech": 63, "gaze": null },
        "transcript": "저는 데이터가 쌓이고 흐르는 구조에...",
        "duration_sec": 46.2,
        "word_count": 138,
        "was_timeout": false,
        "had_reask": true
      }
    ],

    "resilience": {
      "score": 58,
      "display": 3,
      "comment": "압박 질문 이후 답변 길이가 절반으로 줄었습니다."
    },

    "company_comment": "도전과 협업을 강조하는 인재상에 비추어...",

    "improved_answers": [
      {
        "question_id": "q_3",
        "original_excerpt": "낙관적 락을 썼습니다.",
        "suggestion": "선택 이유와 대안 비교를 함께 언급하면...",
        "t_start": 12.4,
        "t_end": 19.8
      }
    ]
  }
}
```

### 필드 규칙

| 필드 | 규칙 |
| --- | --- |
| `report_status` | `complete` \| `partial` |
| `overall.score` | 0~100 정수. 게이트 적용 후 최종값 |
| `overall.display` | 1~5 정수 |
| `overall.gated` | 적절성 게이트가 발동했는가 |
| `overall.partial` | 실패한 축이 있는가 |
| `axes.*.status` | `ok` \| `failed` |
| `axes.*.score` | 실패 시 null |
| `metrics` | 축별 세부 지표. **이번에는 빈 객체** |
| `evidence` | 감점·강점 근거 배열. 항상 존재 (비어 있을 수 있음) |
| `axes.*.error_code` | `status`가 `failed`일 때만 나온다. `ok`·`skipped`에는 필드가 없다 |
| `axes.*.reason` | `status`가 `skipped`일 때만 나온다 |
| `resilience` | **친절형은 항상 null.** 압박 구간이 없어 산출 불가 |
| `company_comment` | 회사 미선택이면 null |

`metrics`가 비어 있는 것은 오류가 아니다.
축별 지표가 확정되는 대로 키가 추가되며, 기존 필드는 바뀌지 않는다.

### evidence 객체

```
question_id   어느 답변에 대한 근거인가
t_start       해당 답변 오디오 기준 시작 초 (소수 1자리)
t_end         종료 초
kind          "strength" | "weakness"
label         짧은 분류명 (화면 배지용)
comment       설명 문장
```

**`t_start`와 `t_end`는 필수다.**
근거 표시 기능이 P1이더라도 값은 항상 채워 보낸다.

---

## 5. 적절성 게이트

주제에서 벗어난 유창한 답변이 말하기·시선 점수만으로
높은 총점을 받는 것을 막는 장치다.

```
내용 관련성이 임계값 미만이면 총점에 상한을 씌운다
```

```json
"overall": {
  "score": 40,
  "display": 3,
  "gated": true,
  "gate_reason": "content_relevance_low"
}
```

`gated`가 true이면 프론트는 총점 옆에 안내 문구를 표시한다.
점수가 낮은 이유가 말하기나 시선이 아니라 내용이라는 것을 알려야 한다.

### 상한은 두 단계다

```
내용 관련성 50 이상    게이트 없음. 총점 그대로
내용 관련성 30~49      부분 이탈. 총점 상한 70
내용 관련성 30 미만    완전 이탈. 총점 상한 40
```

사람이 매기는 5단계 라벨과 같은 자를 쓴다.

```
5점 우수      85~100
4점 양호      70~84
3점 중간      50~69     게이트 없음
2점 미흡      30~49     상한 70
1점 주제이탈   0~29      상한 40
```

**두 단계 모두 `gate_reason`은 `content_relevance_low`로 같다.**
어느 단계인지는 `overall.score`와 `axes.content.score`로 구분한다.
값을 나눠야 할 필요가 생기면 그때 합의해서 추가한다.

**임계값과 상한값은 잠정이며 앵커 답변 세트로 튜닝한다.**
이 값이 바뀌어도 응답 구조는 바뀌지 않는다.

---

## 6. 부분 실패 처리

축 하나가 실패해도 리포트를 만든다.

```
content 실패   전체 실패로 처리한다
speech 실패    남은 축으로 재정규화
gaze 실패      남은 축으로 재정규화
```

**`content`만 필수인 이유는 게이트 때문이다.**
내용 관련성이 없으면 게이트가 작동하지 않아 총점을 신뢰할 수 없다.

**`content` 실패는 리포트 본문을 만들지 않는다.**
부분 리포트로 내보내면 게이트가 돌지 않은 총점이 나가게 되므로,
폴링 결과를 `error`로 남긴다.

```json
{
  "status": "error",
  "error_code": "CONTENT_FAILED",
  "message": "내용 분석에 실패해 리포트를 만들지 못했습니다"
}
```

`result`가 아예 없다. 백엔드는 `status`를 먼저 보고 분기해야 한다.
`speech`·`gaze` 실패는 이와 달리 `status: done`에 `report_status: partial`로 나간다.

### 재정규화

축 가중치를 남은 축에 비례 배분한다.

```
정상       content 0.5   speech 0.3   gaze 0.2
gaze 실패  content 0.625 speech 0.375
```

가중치는 **잠정값이며 검증 후 확정한다.**

재정규화가 일어나면 `overall.partial`이 true가 되고
`axes_used`, `axes_failed`에 어느 축이 쓰였는지 담긴다.

### 시선 축이 없는 정상 케이스

카메라를 끄고 면접한 경우는 실패가 아니다.

```json
"gaze": {
  "status": "skipped",
  "reason": "no_video",
  "score": null
}
```

`failed`와 `skipped`를 구분한다.
화면에서 전자는 "분석 실패", 후자는 "카메라 미사용"으로 표시한다.

---

## 7. 실패한 축만 재시도

```
POST /ai/sessions/{session_id}/report/retry
Idempotency-Key: rpt_sess9f2a1c_02
```

```json
{
  "axes": ["gaze"],
  "answers": [ ... ]
}
```

성공한 축은 다시 계산하지 않는다.
**STT 결과는 `session_id + question_id`로 캐시되어 재사용된다.**
Whisper를 다시 돌리지 않으므로 재시도 비용이 낮다.

응답은 요청한 축만 담아서 돌려준다.
백엔드가 기존 리포트에 병합한다.

---

## 8. 회차 비교

점수 비교는 백엔드가 할 수 있다. AI는 코멘트와 추이 해석을 생성한다.

```
POST /ai/reports/compare
```

**회차 수 제한은 없다.** 배열에 담긴 만큼 전부 분석한다.

```json
{
  "reports": [
    { "session_id": "sess_a", "round": 1, "report": { ... } },
    { "session_id": "sess_b", "round": 2, "report": { ... } },
    { "session_id": "sess_c", "round": 3, "report": { ... } },
    { "session_id": "sess_d", "round": 4, "report": { ... } }
  ]
}
```

`round`는 1부터 시작하는 정수이며 배열은 오름차순으로 보낸다.
AI는 리포트를 저장하지 않으므로 백엔드가 전부 함께 보낸다.
재연습의 `replay_log`와 같은 원칙이다.

### 응답

```json
{
  "latest_round": 4,
  "compared_rounds": [1, 2, 3, 4],

  "vs_previous": {
    "from_round": 3,
    "to_round": 4,
    "overall_delta": 6,
    "axis_delta": { "content": 8, "speech": 5, "gaze": -2 },
    "improved": ["q_1", "q_4"],
    "declined": ["q_7"],
    "unchanged": ["q_2"],
    "comment": "지원동기 문항에서 구체적 사례가 추가되어 내용 점수가 올랐습니다."
  },

  "trend": {
    "overall": [58, 64, 66, 72],
    "axes": {
      "content": [55, 63, 66, 74],
      "speech": [60, 66, 67, 72],
      "gaze": [61, 64, 65, 63]
    },
    "comment": "내용 축은 꾸준히 상승했으나 시선 축은 2회차 이후 정체 상태입니다.",
    "stalled_axes": ["gaze"],
    "best_round": 4
  },

  "by_question": [
    {
      "question_id": "q_1",
      "text": "백엔드 개발 직무에 지원하신 이유를 말씀해 주세요.",
      "category": "지원동기",
      "scores": [62, 70, 71, 78],
      "delta_from_previous": 7,
      "delta_from_first": 16,
      "comment": "회차를 거듭할수록 사례가 구체화되었습니다."
    }
  ]
}
```

### 필드 규칙

| 필드 | 설명 |
| --- | --- |
| `vs_previous` | **직전 회차 대비.** 성장 추이 화면의 기본 표시 |
| `trend` | 전체 회차 흐름. 배열 길이는 `compared_rounds`와 같다 |
| `trend.stalled_axes` | 최근 3회차 변화가 임계 미만인 축. 없으면 빈 배열 |
| `by_question.scores` | 회차별 점수 배열. 그대로 꺾은선 그래프에 쓸 수 있다 |
| `delta_from_previous` | 직전 회차 대비 변화 |
| `delta_from_first` | 1회차 대비 누적 변화 |

`reports`가 1개면 `vs_previous`가 null이 되고 `trend` 배열 길이는 1이다.
회차가 하나뿐일 때도 오류가 아니다.

### 비교 대상 규칙

**`is_replay`가 true인 문항만 비교한다.**

```
비교 가능   is_replay: true    1회차와 동일한 주질문
비교 불가   is_replay: false   꼬리질문, 대체 질문
```

`is_spare_topic`은 필터에 쓰지 않는다.
1회차 예비 토픽 주질문은 `is_spare_topic: true`이면서 `is_replay: true`이므로
비교 대상이다.

**모든 회차가 1회차 질문 세트를 그대로 재생하므로 문항이 항상 일치한다.**
재연습 시 `replay_log`를 항상 1회차 기준으로 보내는 이유가 이것이다.
직전 회차를 기준으로 재생하면 회차마다 질문이 불어나 비교가 불가능해진다.

### 부분 리포트 처리

`report_status`가 `partial`인 회차는 **축 구성이 다르므로 별도로 다룬다.**

```
overall     비교에서 제외한다. 재정규화된 총점은 다른 회차와 스케일이 다르다
축별·문항별  성공한 축은 비교에 포함한다
```

응답에 어느 회차가 부분 리포트였는지 표시한다.

```json
"partial_rounds": [2]
```

`trend.overall` 배열에서 해당 회차는 null이 된다.

```json
"overall": [58, null, 66, 72]
```

프론트는 그래프에서 이 지점을 끊거나 점선으로 표시한다.

---
## 9. 에러 코드

```
INVALID_REQUEST       일반 검증 오류. Idempotency-Key 누락 등     400
INVALID_ANSWERS       answers 배열 형식 오류 또는 빈 배열          400
REPORT_TOO_SHORT      답변이 2문항 미만. 리포트를 만들지 않는다   422
CONTENT_FAILED        내용 분석 실패. 전체 실패                  500
SPEECH_FAILED         말하기 분석 실패. 부분 리포트로 진행        —
GAZE_FAILED           시선 분석 실패. 부분 리포트로 진행          —
STT_FAILED            음성 인식 실패                            500
MEDIA_FETCH_FAILED    오디오·영상 다운로드 실패                  500
```

에러 본문 형식은 질문 생성 계약 8장과 같다.

```json
{ "error_code": "REPORT_TOO_SHORT", "message": "..." }
```

`answers` 배열의 스키마 검증 실패는 `INVALID_ANSWERS`로, 그 외 검증 오류는
`INVALID_REQUEST`로 내려간다. 둘 다 400이다.

**`REPORT_TOO_SHORT`는 되묻기를 세지 않은 기준이다.**
되묻기는 원 질문에 합산되므로 독립 문항으로 세지 않는다.
주질문 1개 + 되묻기 1개는 1문항이라 리포트를 만들지 않는다.

`SPEECH_FAILED`와 `GAZE_FAILED`는 HTTP 오류가 아니다.
`status: done`으로 응답하되 해당 축의 `status`가 `failed`가 된다.

### 재시도 분류

| 코드 | 재시도 | 처리 |
| --- | --- | --- |
| `MEDIA_FETCH_FAILED` | 1회 | presigned URL 만료 가능성. 새 URL로 재요청 |
| `CONTENT_FAILED` | 1회 | LLM 일시 오류 가능성 |
| `STT_FAILED` | 없음 | 오디오 자체 문제일 가능성이 높다 |
| `REPORT_TOO_SHORT` | 없음 | 사용자에게 안내 |
| `INVALID_ANSWERS` | 없음 | 클라이언트 버그 |

---

## 10. 백엔드에 요청하는 것

```
1  로그 테이블에 answer_video_url 추가        시선 축에 필요
2  presigned URL 만료 시간
     이력서 파싱용   10분
     영상 분석용     30분 이상   다운로드 + CV 처리 시간 포함
3  리포트 JSON 원본 보관                      AI는 저장하지 않는다
     회차 비교 시 전체 회차를 함께 보내야 하므로
     오래된 회차도 삭제하지 않는다
4  Idempotency-Key 발급 규칙 합의             rpt_{session_id}_{시도번호}
5  폴링 타임아웃 10분                          질문 생성과 다른 값
```

1번이 없으면 시선 축을 아예 만들 수 없다.
현재 답변 제출 계약이 `audio_url`만 받고 있어 영상 경로가 끊겨 있다.

---

## 11. 이번에 확정되지 않는 것

| 항목 | 확정 시기 |
| --- | --- |
| `metrics` 세부 필드 | 축별 담당자가 지표 확정 후 |
| 축 가중치 (0.5 / 0.3 / 0.2) | 앵커 답변 세트 검증 후 |
| 게이트 임계값·상한값 | 앵커 답변 세트 검증 후 |
| `resilience` 산출식 | 압박형 실제 세션 확보 후 |

**전부 값의 문제이며 구조를 바꾸지 않는다.**
백엔드는 이 문서로 테이블과 파싱 코드를 지금 만들 수 있다.
