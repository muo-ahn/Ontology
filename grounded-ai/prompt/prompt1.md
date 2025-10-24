레포 루트를 스캔하고, 아래 구조가 없다면 생성하세요.

root/
├─ api/
│  ├─ main.py
│  ├─ routers/
│  │  ├─ vision.py        # (기존 유지) vLM 캡션/VQA → JSON 정규화
│  │  ├─ graph.py         # (신규) upsert/context
│  │  └─ llm.py           # (신규) V/VL/VGL 분기
│  └─ services/
│     ├─ vlm_runner.py    # (기존) 없으면 생성
│     ├─ llm_runner.py    # (기존) 없으면 생성
│     ├─ graph_repo.py    # (신규) Cypher upsert/query
│     └─ context_pack.py  # (신규) Edge-first 컨텍스트 빌더
├─ scripts/
│  ├─ run_eval.py         # (신규) 3모드 일괄 실험
│  └─ plot_eval.py        # (선택) 결과 시각화
├─ data/medical_dummy/    # (기존) 샘플 이미지/CSV
├─ docker-compose.yml     # (있으면 유지, 없으면 Neo4j 포함 최소본 생성)
├─ Makefile               # (있으면 유지 + target 추가)
└─ README.md              # (업데이트)

각 파일이 없다면 빈 스켈레톤을 생성하고 TODO를 남기세요.
