# Requirements Definition (2025-10-31)

## R1. Graph Context Coverage
- **Goal**: 증거 경로 다양화를 통해 `ctx_paths_len` 을 1~2에 고정시키는 병목을 제거한다.
- **Scope**: `grounded-ai/api/services/graph_repo.py`, `grounded-ai/api/services/context_pack.py`.
- **Acceptance Criteria**
  - 동일 이미지 3회 반복 실행 시 `ctx_paths_len ≥ 4` 이고 HAS_FINDING, LOCATED_IN, SIMILAR_TO/MENTIONS 등 최소 3종 관계가 포함된다.
  - `k` 파라미터 조정 시 패턴별 슬롯(`k_findings`, `k_reports`, `k_similarity` 등) 이 독립적으로 적용되어 경로 구성이 달라진다.
  - `context_paths` 응답에 중복 경로(label, triple 동일)가 제거된다.

## R2. Image Upsert Idempotency
- **Goal**: 동일 `storage_uri` 로 재실행해도 새로운 Image 노드가 생성되지 않도록 보장한다.
- **Scope**: `grounded-ai/api/routers/pipeline.py`, `grounded-ai/api/services/graph_repo.py`.
- **Acceptance Criteria**
  - `storage_uri` 기준 MERGE 가 1차로 수행되고, 기존 노드가 있으면 기존 `image_id` 를 재사용한다.
  - 테스트 모드에서 동일 `storage_uri` 를 5회 업서트해도 `MATCH (i:Image {storage_uri:$u}) RETURN count(i)` 결과가 항상 1이다.
  - 신규 이미지 업서트 후 `SIMILAR_TO` 간선 동기화 시 최소 1건 이상의 연결이 생성된다(실험 모드에서 임계 완화 시).

## R3. Consensus & Language Hygiene
- **Goal**: 합의 출력이 상시 `disagree/low` 로 떨어지는 문제와 다국어/잘림을 제거한다.
- **Scope**: `grounded-ai/api/routers/pipeline.py`, `grounded-ai/api/routers/llm.py`.
- **Acceptance Criteria**
  - 동일 케이스 3회 실행 시 `agreement_score` 평균이 0.30 이상이며, `status` 는 최소 1회 이상 `agree` 를 포함한다.
  - `presented_text` 및 개별 모드 응답이 한국어 문장 한 줄로 제한되고 메타 발화/질문 역질문이 등장하지 않는다.
  - 비한국어 토큰이 탐지되면 후처리 필터가 적용되어 API 응답에 포함되지 않는다.

## R4. Evidence Summary Completeness
- **Goal**: SUMMARY 가 HAS_FINDING/REPORT 에 편향되는 문제를 해소한다.
- **Scope**: `grounded-ai/api/services/graph_repo.py`, `grounded-ai/api/services/context_pack.py`.
- **Acceptance Criteria**
  - SUMMARY 블록에 LOCATED_IN, RELATED_TO, SIMILAR_TO 등 추가 관계가 집계되어 최소 3종 관계가 출력된다.
  - `graph_context.summary` 메트릭은 각 관계별 cnt, avg_conf 를 제공하며 0건일 경우 해당 항목이 제외된다.

## R5. Similarity Exploration Controls
- **Goal**: `SIMILAR_TO` 컷과 후보 수가 경로 확장의 병목이 되지 않도록 실험 구간에서 완화한다.
- **Scope**: `grounded-ai/api/services/similarity.py`, `grounded-ai/api/routers/pipeline.py`.
- **Acceptance Criteria**
  - API 파라미터 또는 환경변수로 similarity threshold, candidate top-k 를 조정할 수 있다(예: 0.25/10).
  - threshold 를 0.25 로 낮추고 top-k 를 10 이상으로 올리면 `similar_seed_images` 응답이 최소 3건 이상을 포함한다.

## R6. Tooling & CI
- **Goal**: 재현성과 회귀 검출을 위한 기반 마련.
- **Scope**: `tests/`, `.github/workflows/`, `grounded-ai/scripts/`.
- **Acceptance Criteria**
  - pytest 골든 스냅샷(그래프 경로 + 합의 메트릭)이 추가되어 `pytest` 실행만으로 회귀 감지가 가능하다.
  - GitHub Actions 또는 등가 CI 워크플로가 정의되고 그래프/LLM 모듈의 기본 검증을 수행한다.
  - `run_eval_dummy.sh` 는 dockerized cypher-shell 모드만 기본 제공하며 로컬 명령 요구 사항을 명확히 안내한다.

## Non-Functional Constraints
- 모든 신규 스크립트/문서는 UTF-8 을 강제하고 Windows/POSIX 환경에서 동일하게 동작해야 한다.
- Neo4j, Docker Compose, pytest 실행 방법을 README/문서에 갱신해 환경 경고를 제거한다.

