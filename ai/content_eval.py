import os
import re
import logging

import anthropic
from pydantic import BaseModel, Field

from ai.llm import _client, model

logger = logging.getLogger("cue.ai.content_eval")

# docs 폴더 내 프롬프트 파일 경로 (이 파일의 상위 폴더의 docs 안)
PROMPT_FILE = os.path.join(
    os.path.dirname(os.path.dirname(__file__)), 
    "docs", 
    "내용채점_프롬프트_초안.md"
)

def load_system_prompt() -> str:
    """Markdown 파일에서 프롬프트 본문과 Few-shot 앵커를 불러옵니다."""
    try:
        with open(PROMPT_FILE, "r", encoding="utf-8") as f:
            content = f.read()
        
        # ``` 로 둘러싸인 블록들을 추출합니다.
        # 첫 번째 블록은 지시사항(프롬프트 본문), 두 번째 블록은 Few-shot 예시입니다.
        blocks = re.findall(r"```\n?(.*?)```", content, re.DOTALL)
        
        system_prompt = ""
        if len(blocks) >= 1:
            system_prompt += blocks[0].strip()
        if len(blocks) >= 2:
            system_prompt += "\n\n" + blocks[1].strip()
            
        if not system_prompt:
            logger.warning("Markdown 파일에서 프롬프트 블록을 찾지 못했습니다.")
            return "당신은 면접 답변의 내용을 채점합니다."
            
        return system_prompt
        
    except Exception as e:
        logger.error("프롬프트 파일을 읽어오는 데 실패했습니다: %s", e)
        return "당신은 면접 답변의 내용을 채점합니다."

# 모듈 로드 시점에 한 번만 파일을 읽어옵니다.
SYSTEM_PROMPT = load_system_prompt()

class ContentScore(BaseModel):
    """채점 결과. 형식을 모델이 지키도록 스키마로 강제한다.

    처음에는 응답에서 정규식으로 「점수: N」을 뽑았는데, 모델이 「35점」이라고
    답해서 매번 파싱에 실패했다. 형식은 프롬프트로 부탁하는 것보다
    스키마로 받는 편이 안전하다.
    """

    score: int = Field(description="0~100 정수")
    reason: str = Field(description="그렇게 매긴 근거 한 문장")


def score_content(question_text: str, answer_text: str) -> int:
    """LLM을 호출하여 답변의 내용을 채점하고 0~100 사이의 점수를 반환합니다."""
    
    user_message = f"질문: {question_text}\n답변: {answer_text}"
    
    try:
        response = _client().messages.parse(
            model=model(),
            # 사고 토큰이 이 한도 안에서 먼저 소모된다. 200이면 사고만 하다 잘려
            # 점수가 안 나온다. effort를 low로 낮춰 사고를 짧게 하고 여유를 둔다.
            max_tokens=1000,
            system=[{
                "type": "text", 
                "text": SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"} # 프롬프트 캐싱 적용
            }],
            messages=[{"role": "user", "content": user_message}],
            # temperature는 쓰지 않는다. claude-sonnet-5에서 제거된 인자라
            # 넣으면 호출이 400으로 실패한다. 일관성은 effort와 체크리스트로 잡는다.
            output_config={"effort": "low"},
            output_format=ContentScore,
        )
        
        parsed = response.parsed_output
        if parsed is None:
            # 여기서 0을 주면 채점 실패가 "주제이탈"로 둔갑해 총점 상한 40이 걸린다.
            # 채점을 못 한 것과 내용이 없는 것은 다르다.
            raise ValueError(
                f"채점 결과를 해석하지 못했습니다 (stop_reason={response.stop_reason})"
            )

        logger.info("채점 %d점 — %s", parsed.score, parsed.reason)
        return max(0, min(100, int(parsed.score)))

    except Exception as e:
        logger.error("내용 채점 API 호출 실패: %s", e)
        raise
