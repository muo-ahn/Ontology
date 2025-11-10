# 🧩 Ontology Spec-Driven Refactor Plan

> 목적: 연구용 코드(main)를 “명세 기반 시스템(spec-driven system)”으로 정리하여
> reproducible experiment + paper appendix + team collaboration 에 모두 적합한 구조로 만들기.

---

## I. 목적과 개요

현재 `grounded-ai/api/routers/pipeline.py` 는 Vision→Graph→LLM 전체 파이프라인을 한 파일에 통합한 상태다.
이를 단계별 spec 기반으로 재조립하여 다음 세 가지를 달성한다:

1. **단일 진입점 유지:** `/pipeline/analyze` 엔드포인트는 그대로 유지.
2. **기능적 결합도 해소:** request parsing / image identification / context orchestration / consensus 계산 / debug payload 를 모듈로 분리.
3. **명세 기반 테스트:** 각 모듈은 명시적인 Pydantic 계약을 갖고, JSON 스냅샷 테스트로 재현성 보장.

---

## II. 신규 디렉터리 구조 제안

```
grounded-ai/
 ├── api/
 │   ├── routers/
 │   │   └── pipeline.py             # orchestration only
 │   └── services/
 │       ├── image_identity.py       # derive image_id, storage_uri, seed lookup
 │       ├── context_orchestrator.py # wrapper around GraphContextBuilder/PackBuilder
 │       ├── consensus.py            # compute_consensus + weighting logic
 │       ├── debug_payload.py        # assemble debug blob + tracing
 │       └── healthcheck.py          # check llm/vlm/neo4j readiness
 ├── graph/
 │   ├── repo.py                     # GraphRepo.from_env()
 │   ├── models.py                   # Pydantic models for Image, Finding, Report, PathRow
 │   └── schema/
 │       └── GRAPH_SCHEMA.md
 ├── docs/
 │   └── refactor/
 │       ├── architecture.md
 │       ├── module_specs.md
 │       ├── graph_schema.md
 │       ├── pipeline_modes.md
 │       ├── testing_strategy.md
 │       ├── migration_checklist.md
 │       └── spec_refactor_plan.md   # (this document)
 └── tests/
     ├── test_consensus_snapshot.py
     ├── test_context_slots.py
     ├── test_pipeline_e2e.py
     └── fixtures/
         └── dummy_image_IMG201.json
```

---

## III. 주요 모듈별 명세

### 1️⃣ `image_identity.py`

| 항목 | 설명                                                            |
| -- | ------------------------------------------------------------- |
| 역할 | 파일명/경로 기반으로 image_id 와 storage_uri 결정                         |
| 입력 | `file_path: str`, `modality: Optional[str]`                   |
| 출력 | `ImageIdentity { id: str, storage_uri: str, seed_hit: bool }` |
| 규칙 | IMG### 형태 우선, seed registry 매핑 우선, 없으면 fallback hashing       |

---

### 2️⃣ `context_orchestrator.py`

| 항목 | 설명                                                          |
| -- | ----------------------------------------------------------- |
| 역할 | GraphContextBuilder/PackBuilder 를 호출하여 컨텍스트 생성              |
| 입력 | `ImageIdentity`, `k_findings`, `k_reports`, `k_similarity`  |
| 출력 | `GraphBundle { summary, facts, paths }` (모두 Typed)          |
| 특징 | slot rebalance, fallback path 생성, dedup, augment summary 포함 |

---

### 3️⃣ `consensus.py`

| 항목    | 설명                                                              |
| ----- | --------------------------------------------------------------- |
| 역할    | V / VL / VGL 3모드 결과 통합, Jaccard + weight 기반 합의                  |
| 입력    | `List[ModeResult]` (각각 text + meta)                             |
| 출력    | `ConsensusResult { text, agreement_score, mode_weights }`       |
| 내부 로직 | Jaccard(text), overlap(findings), modality penalty, graph bonus |

---

### 4️⃣ `debug_payload.py`

| 항목    | 설명                                                                                  |
| ----- | ----------------------------------------------------------------------------------- |
| 역할    | 분석 중 생성된 intermediate 데이터 구성                                                        |
| 포함 필드 | `context_slot_limits`, `finding_fallback_used`, `registry_hit`, `seeded`, `final_k` |
| 출력    | dict(JSON serializable)                                                             |
| 목적    | 실험 reproducibility 및 논문 appendix 로 활용 가능                                            |

---

## IV. 스키마 명세 강화

### `graph/models.py`

```python
class Finding(BaseModel):
    id: str
    type: str
    location: Optional[str]
    confidence: Optional[float]

class ImageNode(BaseModel):
    id: str
    modality: Optional[str]
    storage_uri: str

class ReportNode(BaseModel):
    id: str
    summary: str
    findings: List[Finding]

class PathRow(BaseModel):
    slot: str
    triples: List[str]
    score: float

class GraphBundle(BaseModel):
    summary: List[ReportNode]
    facts: List[Finding]
    paths: List[PathRow]
```

---

## V. 문서 명세 추가

### `docs/refactor/graph_schema.md`

```
(:Image)-[:HAS_FINDING]->(:Finding)
(:Finding)-[:LOCATED_IN]->(:Anatomy)
(:Image)-[:DESCRIBED_BY]->(:Report)
(:Image)-[:SIMILAR_TO]->(:Image)

Properties:
- HAS_FINDING.confidence: float [0-1]
- LOCATED_IN.region: text
- DESCRIBED_BY.summary_length: int
- SIMILAR_TO.score: float [0-1]
```

### `docs/refactor/pipeline_modes.md`

| Mode | Input                     | Evidence                | LLM Context           | Weight |
| ---- | ------------------------- | ----------------------- | --------------------- | ------ |
| V    | Vision only (caption)     | None                    | caption text          | 0.5    |
| VL   | Vision + Language         | caption + report        | extracted findings    | 0.75   |
| VGL  | Vision + Graph + Language | caption + graph context | findings + graph path | 1.0    |

---

## VI. 테스트 전략

| 파일                                 | 목적                | 주요 검증                           |
| ---------------------------------- | ----------------- | ------------------------------- |
| `test_consensus_snapshot.py`       | 모드 간 합의 결과 고정     | IMG201 케이스 기준 Jaccard ≥ 0.7     |
| `test_context_slots.py`            | slot rebalance 확인 | findings 존재 시 k_findings>0      |
| `test_pipeline_e2e.py`             | 전체 흐름 E2E         | dummy 이미지 입력 시 동일 결과 재현         |
| `fixtures/dummy_image_IMG201.json` | 입력 고정             | seeded 그래프 구조 + expected result |

---

## VII. README 업데이트 항목

* ✅ **Non-production Disclaimer**

  > 본 저장소는 의료 영상 데이터를 이용한 연구용 실험 코드이며, 실제 임상 환경에서 사용되어서는 안 됩니다.
* ✅ **System Diagram**

  * Vision Encoder → Caption → Graph Upsert → Context Pack → LLM Answer → Consensus
* ✅ **Spec References**

  * [docs/refactor/graph_schema.md](docs/refactor/graph_schema.md)
  * [docs/refactor/pipeline_modes.md](docs/refactor/pipeline_modes.md)

---

## VIII. 단계별 마이그레이션 체크리스트

| 단계 | 내용                                | 상태 |
| -- | --------------------------------- | -- |
| 1  | pipeline 기능 모듈 분리                 | ☐  |
| 2  | Pydantic 모델 통일                    | ☐  |
| 3  | GRAPH_SCHEMA/PIPELINE_MODES 문서 추가 | ✅ (docs/refactor/* 작성 완료) |
| 4  | 테스트 스냅샷 확립                        | ☐  |
| 5  | README + disclaimer 보강            | ✅ (루트 README 업데이트) |

### Schema fixes (Issues A–C)

| ID | 조치 | 진행 상황 | 근거 |
| -- | --- | --- | --- |
| A (`Image` 제약) | `schema/v1_1/constraints.cypher` 에 `img.image_id` 제약 추가, 모든 seed/migration 에서 동일 키 사용 | ✅ | grounded-ai/schema/v1_1/constraints.cypher, scripts/cyphers/load_all.cypher |
| B (IMG_002 모달리티) | seed + CSV 에서 modality/caption 수정, migration 에서 `IMG_002` 메타 보정 | ✅ | grounded-ai/scripts/cyphers/seed.cypher, data/medical_dummy/imaging.csv, schema/v1_1/migrations_up.cypher |
| C (version vs version_id) | `AIInference.version_id` 필드 통일, CSV + loader + migration 업데이트, RECORDED_WITH 관계 보장 | ✅ | data/medical_dummy/ai_inference.csv, scripts/cyphers/load_all.cypher, schema/v1_1/migrations_up.cypher |

---

## IX. 향후 확장 (옵션)

1. **Async GraphRepo** – aio-neo4j 또는 asyncio driver 기반 전환.
2. **Weighted Consensus tuning** – 학습된 weight 적용 (실험 데이터 기반).
3. **Artifact Registry** – 각 실험 run 의 spec+결과를 JSONL 로 저장.

---

### ✳️ 요약

이 리팩터 플랜은 “Ontology = Spec-Driven Vision-Graph-LLM Pipeline” 구조를 명시적으로 드러내는 설계다.
핵심 목표는 **코드의 재현성과 논문/블로그/후속 연구에서의 투명성 확보**다.
이 spec이 merge되면, 이후의 논문 Part II(Implementation Details) 챕터를 거의 그대로 가져다 쓸 수 있다.
