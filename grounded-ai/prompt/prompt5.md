api/routers/llm.py 를 작성하세요.

- POST /llm/answer
  Body:
  {
    "mode": "V" | "VL" | "VGL",
    "image_id": "IMG_001",
    "caption": "옵션 (V,VL에서 사용)",
    "style": "one_line"  # 간결 요약
  }

  동작:
  - V:  입력 caption을 그대로 한 줄 요약 규칙에 맞춰 정제(LLM 호출 생략 가능)
  - VL: caption을 LLM에 전달 (규칙: 한국어 한 줄, 30자, 추정 금지)
  - VGL: GraphContextBuilder.build_prompt_context(image_id, k=2)
         LLM 프롬프트 템플릿:
         """
         [Graph Context]
         {context}

         [규칙]
         - 위 컨텍스트만 근거로 답하라.
         - 새로운 사실/추정 금지. 불확실하면 '추가 검사 권고'.
         - 출력은 한국어 한 줄(최대 30자). 근거를 괄호로 간단 표기.

         [질문]
         이 영상의 핵심 임상 소견을 요약하라.
         """
  반환: {"answer": "...", "latency_ms": N}
