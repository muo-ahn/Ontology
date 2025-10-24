Makefile에 다음 타깃을 추가:
- make up        : docker compose up -d (neo4j 포함)
- make pull      : ollama 모델 풀(qwen2.5, qwen2-vl)
- make seed      : (옵션) 데이터 시드
- make eval      : python scripts/run_eval.py

README 최상단에 "3단계 실행"을 추가:
1) make up && make pull
2) python scripts/run_eval.py
3) results.csv / summary.json 확인

TL;DR 템플릿도 추가:
"VGL이 V/VL 대비 평균 일관성 +X%, 환각률 -Y% (더미셋 기준)"
(숫자는 실험 후 채움)
