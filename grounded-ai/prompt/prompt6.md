scripts/run_eval.py 를 작성하세요. 기능:

입력:
- data/medical_dummy/images/*.png 를 N장 읽음
- 각 이미지에 대해:
  1) V: /vision/caption → caption (answer_V = caption 그대로 or 간단 정제)
  2) VL: caption → /llm/answer(mode="VL")
  3) VGL:
     - /graph/upsert (case_id = 이미지 파일명 기반)
     - /graph/context (image_id)
     - /llm/answer(mode="VGL", image_id=...)
- 정답셋(간단 키워드 룰베이스): type/location/size 범주 키워드
  - 예: {"nodule","opacity"} / {"RUL","LLL"} / {"1-2cm","2-3cm"} 등
- 지표:
  - factuality@1: 키워드 매칭 여부(0/1)
  - hallucination: 정답셋에 없는 단어 등장 시 1
  - consistency: 동일 입력 3회 생성 후 Jaccard(단어집합) 평균 (V는 1로 처리)
  - latency_ms: 모드별 평균

출력:
- results.csv: image_id, mode, factuality, hallucination, consistency, latency_ms
- summary.json: 모드별 평균 요약
- (선택) plot_eval.py에서 막대 그래프 PNG 저장
