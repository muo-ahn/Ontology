# 🧠 Ontology × vLM × LLM Prototype

> ⚠️ **Non-production Disclaimer**  
> 본 저장소는 의료 영상 데이터를 이용한 연구용 실험 코드이며, 실제 임상 환경에서 사용되어서는 안 됩니다.  
> 코드를 실행하는 경우, 출력은 연구 참고용으로만 활용해 주세요.

## Overview

이 프로젝트는 **LLM이 데이터의 의미를 이해하도록 만드는 방법**을 탐구한다.  
단순한 텍스트 예측을 넘어서, 실제 세계의 구조를 **Ontology(의미 관계)** 와 **vLM(시각적 맥락)** 을 통해 연결하는 것이 목표다.

---

## Why Ontology?

- 데이터 필드나 명칭이 달라도, 같은 개념이라면 **의미적으로 매핑**할 수 있어야 한다.
- Ontology는 이런 의미적 연결을 제공하고, 모델이 **데이터의 구조와 맥락**을 이해하도록 돕는다.
- LLM이 만든 결과를 **Neo4j 기반 Knowledge Graph** 에 반영해 일관성을 유지한다.

---

## Why vLM?

- 세상은 텍스트만으로 표현되지 않는다.
- vLM(Visual Language Model)은 이미지와 텍스트를 함께 이해해, LLM이 **언어 외적 근거(visual grounding)** 를 갖게 한다.
- 즉, **“보는 것”을 “이해하는 것”으로 연결**한다.

---

## Combined Effect

| 구성 요소    | 역할        |
| ------------ | ----------- |
| **LLM**      | 언어적 추론 |
| **vLM**      | 시각적 근거 |
| **Ontology** | 의미적 구조 |

이 세 가지를 결합해, 모델이 단순 언어 모형이 아닌  
**“의미 기반의 통합 지능(Grounded Intelligence)”** 으로 작동하도록 실험한다.

---

## 진행 현황 (2025-11-16)

- **Spec-D (그래프 기반 합의)**: `consensus.vote_summary`, `agreement_components`, `mode_weights` 를 응답에 노출하고, 2/3 미만 합의 시 `confidence=low`, `notes` 에 `limited mode agreement` 를 자동 기록한다. `graph_paths_strength` 가 0이면 V/VL이 즉시 `degraded="graph_mismatch"` 로 다운그레이드된다.
- **Spec-E (경로 증거 복원)**: `GraphRepo.query_paths()` 가 Neo4j 의 `HAS_FINDING / LOCATED_IN / SIMILAR_TO` 경로를 multi-hop 으로 수집하고, `GraphContextBuilder` 는 경로가 없을 때만 facts 기반 fallback 을 합성한다. `graph_context.paths` 와 `debug.context_paths_head` 에 실제 triple 이 노출되며 `context_fallback_used=false` 를 유지한다.
- **디버그 스크립트 검증**: `./scripts/vision_pipeline_debug.sh … '{"force_dummy_fallback": true}'` 로 IMG_001 · IMG_003 · IMG201 케이스를 실행하면 `graph evidence boosted consensus (paths_signal=0.23)` 메모, `findings slot rebalanced from 1 to 2` 노트, 그리고 Neo4j 경로를 확인할 수 있다.
- **테스트 & 툴링**: Spec-D/E 회귀는 `pytest tests/test_normalizer.py tests/test_debug_payload.py tests/test_context_orchestrator.py tests/test_paths_and_analyze.py tests/test_consensus.py tests/test_consensus_snapshot.py` 및 `PYTHONPATH=grounded-ai/api python scripts/check_label_drift.py` 로 커버한다.

---

## System Diagram

```
Vision Encoder → Caption Normalizer → Graph Upsert → Graph Context Pack
      ↓                                             ↓
   Vision Mode ───────────────┐          Graph bundle + Findings
                              ├─> LLMS (V / VL / VGL) → Consensus Core → Debug Payload
   Vision+Language Mode ──────┘
```

- `/pipeline/analyze` 는 위 단계를 순차적으로 호출하는 단일 진입점이다.
- Graph Context Pack 은 Neo4j 에서 summary/facts/paths 를 생성해 VGL 모드를 지원한다.
- Consensus Core 는 V/VL/VGL 결과를 집계해 agreement score 를 산출한다.

### Debug Payload Reference

`./scripts/vision_pipeline_debug.sh … '{"force_dummy_fallback": true}'` 로 호출하면 FastAPI 응답의 `debug` 필드에 아래 키가 항상 포함된다.

| Stage | 핵심 필드 | 설명 |
| --- | --- | --- |
| `pre_upsert` | `norm_image_id`, `finding_fallback`, `seeded_finding_ids` | 이미지 식별·레지스트리 히트 여부, seed 강제 여부 |
| `context` | `context_summary`, `context_paths_head`, `graph_paths_strength`, `similar_seed_images` | GraphContextBuilder 결과와 fallback 경로/슬롯 |
| `consensus` | `mode_weights`, `agreement_components`, `anchor_mode_used`, `conflict_modes` | V/VL/VGL 가중치, 텍스트/구조/그래프 기여도, 모달리티 패널티 |
| `evaluation` | `finding_source`, `seeded_finding_ids`, `status` | 응답 공개용 요약 및 degraded 여부 |

이 덕분에 “그래프 업서트 실패”, “모달리티 충돌”, “graph bonus 적용” 같은 사건을 한눈에 추적할 수 있다.

---

## Dataset

- 의료 도메인 더미 데이터 (Patient, Encounter, Observation, Diagnosis, Medication, Image, AIInference)
- Neo4j로 관계형 그래프 구축
- vLM(VQA/Caption) → LLM 추론 → Ontology 반영 구조

---

## Goal

> 데이터와 언어, 감각이 분리되지 않는 **“이해 가능한 AI”** 를 만드는 첫 단계.

---

## How to Try It

> **TL;DR template**  
> `VGL이 V/VL 대비 평균 일관성 +X%, 환각률 -Y% (더미셋 기준)`
> (실험 실행 후 `scripts/run_eval.py` 결과로 X/Y를 채워 넣으세요.)

### 0. pipeline/analyze

```sh
./scripts/vision_pipeline_debug.sh "/data/medical_dummy/images/api_test_data/Ultrasound-fatty-liver-Ultrasound-of-the-whole-abdomen-showing-increased-hepatic.png" "{}"
```

or

```sh
./scripts/vision_pipeline_debug.sh "/data/medical_dummy/images/api_test_data/Ultrasound-fatty-liver-Ultrasound-of-the-whole-abdomen-showing-increased-hepatic.png" '{"force_dummy_fallback": true}'
```

### 1. 건강 상태 확인

```sh
curl http://localhost:8000/health
```

### 2. vLM 캡션 정규화 (이미지 → JSON)

```sh
curl -X POST http://localhost:8000/vision/caption \
  -H "Content-Type: application/json" \
  -d '{
        "file_path": "grounded-ai/data/medical_dummy/images/img_001.png",
        "image_id": "IMG_001"
      }'
```

- 응답 예시:
  ```json
  {
    "image": { "id": "IMG_001", "path": "/data/img_001.png", "modality": "XR" },
    "report": {
      "id": "r_83fd0c4a",
      "text": "Chest X-ray – probable right upper lobe nodule (~1.8 cm).",
      "model": "qwen2-vl",
      "conf": 0.83,
      "ts": "2025-10-23T12:00:00.000000+00:00"
    },
    "findings": [
      {
        "id": "f_1c72a5aa2a5d",
        "type": "nodule",
        "location": "RUL",
        "size_cm": 1.8,
        "conf": 0.87
      }
    ],
    "vlm_latency_ms": 742
  }
  ```

### 3. 그래프 업서트 (노드 + 엣지 강제 생성)

```sh
curl -X POST http://localhost:8000/graph/upsert \
  -H "Content-Type: application/json" \
  -d '{
        "case_id": "CASE_DEMO_001",
        "image": {
          "id": "IMG_001",
          "path": "/data/img_001.png",
          "modality": "XR"
        },
        "report": {
          "id": "rep_demo_001",
          "text": "Chest X-ray – probable RUL nodule (~1.8 cm).",
          "model": "qwen2-vl",
          "conf": 0.83,
          "ts": "2025-10-23T12:00:00Z"
        },
        "findings": [
          {
          "id": "f_1c72a5aa2a5d",
          "type": "nodule",
          "location": "RUL",
          "size_cm": 1.8,
          "conf": 0.87
        }
      ]
    }'
```

- 업서트는 `HAS_IMAGE`, `HAS_FINDING`, `DESCRIBED_BY` 엣지를 모두 포함한다.

### 4. 그래프 컨텍스트 조회 (엣지 기반 직렬화)

```sh
curl "http://localhost:8000/graph/context?image_id=IMG_001&mode=triples&k=2"
```

- 응답은 `[EDGE SUMMARY]`, `[EVIDENCE PATHS]`, `[FACTS JSON]` 섹션이 포함된 단일 문자열. `mode=json`으로 호출하면 Facts JSON만 반환된다.

### 5. LLM 최종 소견 (V / VL / VGL 비교)

```sh
curl -X POST http://localhost:8000/llm/answer \
  -H "Content-Type: application/json" \
  -d '{"mode": "VGL", "image_id": "IMG_001", "style": "one_line"}'
```

- `mode`: `V`(vLM 캡션 정제), `VL`(캡션→LLM), `VGL`(그래프 컨텍스트 기반).
- `VL` 요청 시에는 `caption` 필드를 함께 전달해야 한다.
- 응답 형식: `{"answer": "...", "latency_ms": ...}`.

### 6. 비동기 파이프라인 (선택)

1. **작업 생성**
   ```sh
   curl -X POST http://localhost:8000/vision/tasks \
     -F "prompt=Summarize the key findings in this X-ray." \
     -F "image=@grounded-ai/data/medical_dummy/images/img_002.png" \
     -F "persist=true"
   ```
2. **SSE 스트림 감시**
   ```sh
   curl -N http://localhost:8000/vision/tasks/<task_id>/events
   ```
   → Redis Streams 워커가 `queued → vision → graph → llm` 순으로 이벤트를 보낸다.

### 7. 추가 그래프 질의

```sh
curl -X POST http://localhost:8000/graph/cypher \
  -H "Content-Type: application/json" \
  -d '{"query": "MATCH (i:Image)-[r:HAS_FINDING]->(f:Finding) RETURN i.id AS image, f.type AS finding LIMIT 5"}'
```

### 8. vision pipeline debug 질의

```bash
curl -sS -X POST "http://localhost:8000/pipeline/analyze?sync=true&debug=1" \
  -H 'Content-Type: application/json' \
  -d '{
        "file_path":"/data/medical_dummy/images/api_test_data/Ultrasound-fatty-liver-Ultrasound-of-the-whole-abdomen-showing-increased-hepatic.png",
        "modes":["V","VL","VGL"],
        "k":2,
        "max_chars":120
      }' \
  | jq '.debug'
```

### 9. debug

```bash
curl -sS -X POST "http://localhost:8000/pipeline/analyze?sync=true&debug=1" \
  -H 'Content-Type: application/json' \
  -d '{
        "file_path": "/data/medical_dummy/images/api_test_data/Ultrasound-fatty-liver-Ultrasound-of-the-whole-abdomen-showing-increased-hepatic.png",
        "modes": ["V","VL","VGL"],
        "k": 2,
        "max_chars": 120,
        "parameters": {"force_dummy_fallback": true}
      }' \
  | jq '{finding_fallback: .debug.finding_fallback, finding_source: .results.finding_source, seeded_ids: .results.seeded_finding_ids}'
```

### 10. /pipeline/analyze e2e test

sync true

```bash
curl -sS -X POST "http://localhost:8000/pipeline/analyze?sync=true&debug=1" \
  -H 'Content-Type: application/json' \
  -d '{
        "file_path": "/data/medical_dummy/images/api_test_data/Ultrasound-fatty-liver-Ultrasound-of-the-whole-abdomen-showing-increased-hepatic.png",
        "modes": ["V","VL","VGL"],
        "k": 2,
        "max_chars": 120,
        "parameters": {"force_dummy_fallback": true}
      }'
```

```bash
curl -sS -X POST "http://localhost:8000/pipeline/analyze?sync=true&debug=1" \
  -H 'Content-Type: application/json' \
  -d '{
        "file_path": "/data/medical_dummy/images/api_test_data/Ultrasound-fatty-liver-Ultrasound-of-the-whole-abdomen-showing-increased-hepatic.png",
        "modes": ["V","VL","VGL"],
        "k": 2,
        "max_chars": 120
      }' \
  | jq '{slots: .debug.context_slot_limits, paths: .graph_context.paths}'

---

## Spec References

- [docs/refactor/architecture.md](docs/refactor/architecture.md) – 파이프라인 계층과 책임 정의
- [docs/refactor/module_specs.md](docs/refactor/module_specs.md) – 서비스/모듈 계약
- [docs/refactor/graph_schema.md](docs/refactor/graph_schema.md) – Neo4j 스키마 및 제약
- [docs/refactor/pipeline_modes.md](docs/refactor/pipeline_modes.md) – V/VL/VGL 모드 및 합의 정책
- [docs/refactor/testing_strategy.md](docs/refactor/testing_strategy.md) – 테스트/CI 전략
- [docs/refactor/migration_checklist.md](docs/refactor/migration_checklist.md) – 리팩터 진행 체크리스트
```
