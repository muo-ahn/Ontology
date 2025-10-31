# Action Plan (Spec-Driven)

## Phase 1 — Graph Schema & Context Restoration
- **Targets**: R1 (Graph Context), R2 (Image Upsert), R4 (Summary).
- **Steps**
  1. Refactor `TOPK_PATHS_QUERY` into UNION 기반 패턴 세트(P1: 직접 HAS_FINDING/LOCATED_IN, P2: DESCRIBED_BY→MENTIONS, P3: SIMILAR_TO multi-hop)와 패턴별 LIMIT.
  2. `ContextPack` 빌더에서 dedup 및 패턴별 k 조정 로직을 추가하고 SUMMARY 출력에 신규 관계 집계 반영.
  3. `pipeline.upsert_case` 경로에서 `storage_uri` 우선 MERGE → 기존 `image_id` 재사용, `storage_uri_key` 는 분기 로깅만 남김.
  4. 회귀 확인: 이미지 재업서트 5회 반복, 패턴 카운트 Cypher 3종 실행으로 ctx_paths 및 SUMMARY 를 검증.
- **Deliverables**
  - 업데이트된 Cypher 쿼리 및 Python 서비스.
  - `tests/` 내 그래프 컨텍스트 단위 테스트(골든 스냅샷 초안).
  - Phase 1 검증 로그.

## Phase 2 — Consensus & Language Reliability
- **Targets**: R3 (Consensus), R4 (Summary follow-up).
- **Steps**
  1. `compute_consensus` 를 2/3 투표 + 모드 가중치(가중치 테이블 외부 설정) 구조로 재설계, outlier 억제 및 fallback 상태 명확화.
  2. LLM 템플릿을 한국어 only, 금지 표현/다국어 필터 명시로 재작성하고 인코딩 깨짐(�) 제거.
  3. 응답 후처리에서 비한국어 토큰, 잘림 문자를 제거하고 `presented_text` 를 안정적 포맷으로 통일.
  4. 로그/평가에서 `agreement_score` 개선 여부를 반복 측정(동일 샘플 3회 평균 ≥0.30).
- **Deliverables**
  - 업데이트된 파이프라인/LLM 모듈.
  - 합의 로직 유닛 테스트(Jaccard/가중치 케이스).
  - 언어 필터 스냅샷 테스트.

## Phase 3 — Similarity Exploration & Controls
- **Targets**: R5.
- **Steps**
  1. `compute_similarity_scores` 파라미터를 노출하고 API에서 실험 모드 default(예: threshold 0.25, top_k 12) 옵션 제공.
  2. 컨텍스트 빌더에서 새로운 SIMILAR_TO 결과를 경로 확장에 반영하도록 업데이트.
  3. 실험 로그를 통해 `similar_seed_images` ≥3건, 경로 확장 증가 확인.
- **Deliverables**
  - 환경/요청 파라미터 문서화.
  - SIMILAR_TO 기반 경로 확장 테스트.

## Phase 4 — Tooling & CI Enablement
- **Targets**: R6.
- **Steps**
  1. pytest 골든 스냅샷(Phase 1~3 출력)과 property-based 테스트 추가.
  2. GitHub Actions 워크플로(`.github/workflows/ci.yaml`) 작성: lint → pytest → docker compose smoke.
  3. `run_eval_dummy.sh` 및 README 갱신: dockerized cypher-shell 기본화, Compose version 키 제거.
- **Deliverables**
  - CI 파이프라인 최초 실행 로그.
  - README/스크립트 업데이트.

## Spec-Driven Tracking
- 각 Phase 완료 시 요구사항 번호(R1~R6)에 대한 검증 절차와 결과를 `docs/spec-status.md`에 기록한다(초기 파일은 Phase 1 착수 전에 생성).
- 테스트/로그 증적은 `logs/` 디렉토리에 타임스탬프와 함께 보관해 회귀 진단 자료로 사용한다.

