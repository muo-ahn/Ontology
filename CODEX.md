당신은 코드 작성형 에이전트입니다. 다음 목표를 달성하세요.

[목표]
- vLM → GraphDB(Neo4j) → LLM 파이프라인에서, "엣지 중심(Edge-first) 컨텍스트"를 생성하고 LLM 프롬프트에 주입하도록 구현.
- 3가지 모드 비교:
  - V: vLM만 사용
  - VL: vLM → LLM (그래프 미사용)
  - VGL: vLM → Neo4j(Graph Context) → LLM
- run_eval.py로 일괄 실험 → results.csv 생성

[환경 변수(없으면 기본값 사용)]
- OLLAMA_HOST=http://localhost:11434
- LLM_MODEL=qwen2.5:7b-instruct-q4_K_M
- VLM_MODEL=qwen2-vl:2b-instruct-q4_0
- NEO4J_URI=bolt://localhost:7687, NEO4J_USER=neo4j, NEO4J_PASS=neo4j

[수용 기준]
- /api/routers/graph.py: /graph/upsert, /graph/context 가 존재
- /api/services/context_pack.py: Edge Summary + Top-k Evidence Paths + Facts JSON 생성
- /api/routers/llm.py: /llm/answer 가 V/VL/VGL 모드 지원
- scripts/run_eval.py: 비교 실험 및 CSV 저장
- README에 실행법 및 TL;DR 결과 템플릿 추가
