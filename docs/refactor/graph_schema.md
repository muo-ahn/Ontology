# Graph Schema & Constraints

본 문서는 Neo4j 기반 Ontology 그래프의 노드/관계, 속성, 제약 조건, 예시 Cypher 쿼리를 정의한다.
데이터 정합성 이슈(A~C)를 해결하기 위한 요구사항을 명시적으로 포함한다.

---

## 1. Node Definitions

| Label | 필수 속성 | 선택 속성 | 설명 |
| --- | --- | --- | --- |
| `Image` | `image_id`, `storage_uri` | `modality`, `study_date` | 원본 영상 메타 |
| `Finding` | `finding_id`, `type` | `location`, `confidence`, `severity` | 모델 또는 보고서에서 추출된 소견 |
| `Anatomy` | `anatomy_id`, `name` | `region` | 신체 부위 |
| `Report` | `report_id`, `summary` | `author`, `summary_length` | 텍스트 리포트 |
| `OntologyVersion` | `version_id`, `released_at` | `notes` | 그래프 스키마/업서트 버전 |
| `AIInference` | `inference_id`, `version_id`, `created_at` | `mode`, `agreement_score` | 파이프라인 추론 결과 |
| `GraphPath` | `path_id`, `triples` | `slot`, `score`, `seed_image_id` | Persisted graph path snapshot |

---

## 2. Relationship Definitions

| 관계 | 방향 | 필수 속성 | 설명 |
| --- | --- | --- | --- |
| `(:Image)-[:HAS_FINDING]->(:Finding)` | Image → Finding | `confidence ∈ [0,1]` | 이미지에서 발견된 소견 |
| `(:Finding)-[:LOCATED_IN]->(:Anatomy)` | Finding → Anatomy | `region` | 소견 위치 |
| `(:Image)-[:DESCRIBED_BY]->(:Report)` | Image → Report | `summary_length` | 리포트 요약 |
| `(:Image)-[:SIMILAR_TO]->(:Image)` | Image ↔ Image | `score ∈ [0,1]` | 유사도 |
| `(:AIInference)-[:BACKED_BY]->(:GraphPath)` | Inference → Path | `weight` | 그래프 근거 |
| `(:AIInference)-[:USES_VERSION]->(:OntologyVersion)` | Inference → Version | 없음 | 버전 추적 |

`GraphPath` nodes persist context/evidence paths with required `path_id` and `triples` (string list).
Optional `slot`, `score`, and `seed_image_id` fields capture ranking context so `AIInference` evidence can be replayed or audited.

---

## 3. Constraints & Indexes

```cypher
CREATE CONSTRAINT image_id_unique IF NOT EXISTS
FOR (img:Image) REQUIRE img.image_id IS UNIQUE;

CREATE CONSTRAINT finding_id_unique IF NOT EXISTS
FOR (f:Finding) REQUIRE f.finding_id IS UNIQUE;

CREATE CONSTRAINT anatomy_id_unique IF NOT EXISTS
FOR (a:Anatomy) REQUIRE a.anatomy_id IS UNIQUE;

CREATE CONSTRAINT report_id_unique IF NOT EXISTS
FOR (r:Report) REQUIRE r.report_id IS UNIQUE;

CREATE CONSTRAINT version_id_unique IF NOT EXISTS
FOR (v:OntologyVersion) REQUIRE v.version_id IS UNIQUE;

CREATE INDEX ai_inference_mode IF NOT EXISTS
FOR (ai:AIInference) ON (ai.mode);
```

> ⚠️ **Issue A 해소:** Seed 스크립트는 반드시 `MERGE (img:Image {image_id: $id})` 패턴을 사용해 제약과 일치해야 한다.

---

## 4. Seed Data Rules

1. `modality` 와 caption 내용이 불일치하지 않도록 검증(예: ECG ↔ 초음파).
2. `AIInference.version_id` 필드를 사용해 `OntologyVersion.version_id` 와 직접 연결한다.
3. 모든 seed 업서트는 `MERGE ... ON CREATE SET ... ON MATCH SET ...` 패턴을 따라 idempotent 하게 설계한다.

---

## 5. Example Cypher Queries

### 5.1 Context Paths

```cypher
MATCH p = (img:Image {image_id: $image_id})
          -[:HAS_FINDING]->(:Finding)-[:LOCATED_IN]->(:Anatomy)
RETURN p AS path
ORDER BY reduce(score = 0, rel IN relationships(p) | score + coalesce(rel.score, 0)) DESC
LIMIT $max_paths;
```

### 5.2 Similar Image Expansion

```cypher
MATCH (seed:Image {image_id: $image_id})-[rel:SIMILAR_TO]->(sim:Image)
RETURN sim.image_id AS similar_id,
       sim.modality,
       sim.storage_uri,
       rel.score AS sim_score
ORDER BY sim_score DESC
LIMIT $k_similarity;
```

---

## 6. GraphBundle Materialization

- `summary`: top `k_reports` 의 `Report.summary` + `HAS_FINDING` confidence histogram.
- `facts`: flatten 된 `Finding` + `LOCATED_IN` 정보.
- `paths`: Cypher path 결과를 `PathRow(slot, triples, score)` 로 변환해 응답에 포함.

`ContextOrchestrator` 는 `GraphRepo.query_bundle(image_id, limits)` 함수를 통해 위 데이터를 얻는다.

---

## 7. Validation & Monitoring

- Nightly job 이 `MATCH (img:Image) WHERE NOT img.image_id STARTS WITH 'IMG' RETURN img` 쿼리로 규칙 위반 탐지.
- Constraints 실패는 CI `test_graph_schema.py` 에서 seed 스크립트 로드 시 검출한다.
- Metrics: `graph.paths.count`, `graph.paths.empty_rate`, `graph.seed.seeded_ratio`.

### 7.1 Recent Fixes (Issues A–C)

- **Image constraint alignment (Issue A):** Constraint + `MERGE` 모두 `image_id` 기준으로 통일하고, `schema/v1_1/migrations_up.cypher` 로 legacy `img.id` 를 제거.
- **Modality/caption fix (Issue B):** `IMG_002` seed + CSV 기록을 초음파(US)로 바로잡고, migration 에 동일 패치 포함.
- **Version field fix (Issue C):** `AIInference.version_id` 를 CSV/loader/migration 전 구간에서 사용하고 `RECORDED_WITH` 관계를 강제.

이 스키마 명세는 `module_specs.md` 와 `pipeline_modes.md` 에 정의된 서비스/모드 로직의 근거를 제공한다.
