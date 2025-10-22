### “지식 그래프와 시각 언어 모델을 활용한 대형언어모델의 의미 기반 추론 구조 연구”

1. 서론
   - 연구 배경: LLM의 한계 (비구조적, 환각, 관계 이해 부족)
   - 연구 필요성: 의미적 이해와 데이터 구조의 결합 필요
   - 연구 목적: Ontology + vLM + LLM의 융합으로 “Grounded Reasoning” 구현

2. 관련 연구
   - LLM 구조 개요 및 한계
   - Ontology / Knowledge Graph / Graph-RAG 개념
   - Vision-Language Model (vLM) 개요
   - 기존 연구 비교

3. 시스템 설계 및 구현
   - 전체 아키텍처 (FastAPI + Ollama + Neo4j + Streamlit)
   - 데이터셋(의료 더미 데이터) 구성
   - Ontology 설계 (Entity / Relationship 구조)
   - Graph Context Injection 방식 (Cypher 결과 → Prompt)

4. 실험
   - vLM을 통한 캡션 생성 및 의미 추출
   - LLM 추론 정확도/일관성 비교 (Ontology 컨텍스트 유무)
   - 그래프 확장 및 질의 예시

5. 결과 및 논의
   - Ontology 주입 전후 비교
   - 모델의 일관성/근거성 향상 분석
   - 한계점 및 개선 방향

6. 결론
   - 의미 기반 추론(semantic reasoning)의 가능성
   - 향후 연구: Ontology-LORA fine-tuning, Multimodal Graph Integration
