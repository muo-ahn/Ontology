# Dummy Pipeline Remediation Specification

## 1. Scope
- Align the dummy evaluation pipeline with the pre-seeded Neo4j graph so that seeded findings and reports are surfaced during analysis.
- Eliminate downstream symptoms caused by missing graph context when the analyzer operates on dummy assets.
- Capture acceptance criteria that can be automated after implementation.

## 2. Dependencies & Constraints
- Applies to the dummy dataset used by `run_eval_dummy.sh` and `/pipeline/analyze`.
- Neo4j seed files (`seed.cypher`, `seed_dummy_C.cypher`) define the source of truth for image identifiers, findings, and report nodes.
- No production data changes are expected; requirements focus on the synthetic evaluation path.

## 3. Definitions
- **Synthetic Image ID**: ID currently derived from incoming filenames by `_derive_image_id_from_path`.
- **Seeded Image ID**: ID assigned in Neo4j seed scripts (`IMG201`, `IMG202`, ...).
- **Slot Budget**: `k_*` limits assigned within `context_pack.py` that govern how many paths per slot are queried.
- **No Graph Evidence**: Pipeline flag triggered when both `facts.findings` and `paths` are empty.

## 4. Functional Requirements

### FR1 — Normalize Image Identifiers
- **Goal**: Ensure inbound dummy image requests resolve to the seeded `Image` nodes.
- **Requirements**
  - R1.1 `_derive_image_id_from_path` must map dummy dataset filenames to the corresponding seeded `image_id` when a lookup exists.
  - R1.2 Requests explicitly providing `image_id` must bypass filename normalization and use the supplied identifier verbatim.
  - R1.3 Synthetic IDs must never be persisted for dummy inputs; storage URIs must match the seeded graph (`/data/dummy/IMG###.png`).
- **Acceptance Criteria**
  - AC1 Running `scripts/run_eval_dummy.sh debug=1` logs `debug.norm_image_id=IMG20X` (matching the seeded nodes) instead of synthetic values.
  - AC2 Canonical storage URIs are verified automatically after analysis: for each dummy lookup hit, the persisted `storage_uri` equals the canonical seeded path (`/data/dummy/IMG###.png`), and the GraphRepo query confirms no alternate URIs were created during the run.
  - AC3 Upserting the same dummy image twice does not create additional `Image` nodes or alter the lookup mapping.

#### FR1 AC2 재설계 메모
- **Canonical Source**  
  Dummy 이미지 레지스트리(`DummyImageRegistry`)를 단일 진실 소스로 사용하여 `image_id -> storage_uri` 테이블을 노출한다. 파이프라인은 업서트 시 해당 매핑을 우선 적용하고, 미지정일 경우에도 동일한 경로를 강제한다.
- **자동 검증 전략**  
  1. `/pipeline/analyze` 호출 후 `debug.storage_uri`가 레지스트리 경로와 일치하는지 확인한다.  
  2. `GraphRepo.from_env()._run_read("MATCH (i:Image {image_id:$id}) RETURN COLLECT(i.storage_uri)")`를 통해 동일 ID에 여러 URI가 존재하지 않음을 검증한다.  
  3. 새로운 pytest 케이스(`test_storage_uri_canonical_for_dummy_images`)에서 ①,②를 모두 수행하며, 기존 그래프 상태는 테스트 종료 시 원복한다.
- **관측 포인트**  
  - `debug.norm_image_id_source`가 `dummy_lookup`인 경우에만 AC2 체크를 실행한다(직접 입력된 `image_id`는 제외).  
  - 실패 시 로그와 테스트 양쪽에서 `storage_uri_mismatch` 메모를 남겨 추적 가능하도록 한다.
- **향후 확장**  
  만약 더미 자산이 추가될 경우, 레지스트리를 기반으로 한 스냅샷 테스트를 추가하여 모든 ID에 대해 일괄 확인할 수 있도록 한다.

### FR2 — Preserve Findings When Model Output Is Mocked
- **Goal**: Maintain at least one `HAS_FINDING` edge even when the analyzer falls back to mock captions.
- **Requirements**
  - R2.1 `normalizer._fallback_findings_from_caption` must emit a deterministic finding set for known dummy images when real model findings are absent.
  - R2.2 Upsert logic must persist the fallback findings with the linked `Image` node resolved via FR1.
  - R2.3 Fallback captions must be tagged so downstream metrics can differentiate mocked from real findings.
- **Acceptance Criteria**
  - AC4 For dummy runs without real model output, `graph_context.facts.findings` contains at least one entry tied to the seeded findings.
  - AC5 `MATCH (:Image {image_id:'IMG20X'})-[:HAS_FINDING]->(f:Finding)` returns the expected seeded finding IDs after analysis.
  - AC6 The analyzer response includes metadata flagging `finding_source="mock"` (or equivalent) when fallbacks are used.

### FR3 — Rebalance Context Slot Budgets
- **Goal**: Prevent hard allocation of all path budget to findings when none exist.
- **Requirements**
  - R3.1 `_rebalance_slot_limits` must redistribute unused finding budget to `reports` and `similarity` slots when `findings == 0`.
  - R3.2 Slot redistribution must be deterministic and logged under debug mode.
  - R3.3 Context queries must honor the new slot limits and fetch available report paths.
- **Acceptance Criteria**
  - AC7 With `k=2`, dummy analysis retains at least one report slot when findings are empty (`slot_limits.reports >= 1` in debug logs).
  - AC8 For `image_id='IMG20X'`, `graph_context.paths` includes at least one `DESCRIBED_BY` path after analysis.
  - AC9 Manual override `parameters={"k_reports":2}` yields the same paths as automatic rebalance, proving parity.

### FR4 — Prevent Silent Degradation of Ensemble Output
- **Goal**: Avoid falling back to VL mode outputs with placeholder text when graph evidence is missing.
- **Requirements**
  - R4.1 Pipeline must gate VL fallback on an explicit confidence downgrade that annotates the response status (e.g., `status="low_confidence"` instead of `disagree`).
  - R4.2 When FR1–FR3 succeed, VGL mode must remain active and emit the seeded findings and reports.
  - R4.3 Mock LLM responses must interpolate resolved `image_id` values; placeholders `(IMAGE_ID)` must be replaced prior to response.
- **Acceptance Criteria**
  - AC10 With seeded graph alignment restored, analyzer responses surface findings and reports, `agreement_score > 0`, and `status="agree"`.
  - AC11 When graph evidence is still absent (simulated by removing the lookup entry), the response explicitly marks `status="low_confidence"` and explains the fallback.
  - AC12 Placeholder tokens are absent from the final text; logs confirm resolved IDs are injected before emission.

## 5. Non-Functional Requirements
- NFR1 All new logic must be covered by automated tests (unit or integration) runnable via `pytest`.
- NFR2 Debug logging added for these fixes must be guarded by existing debug flags to avoid noisy default output.
- NFR3 Specifications should be validated in CI once automated tests are added (tracked separately under testing/CI backlog).

## 6. Validation Checklist
- [ ] AC1–AC12 verified manually or via automated tests.
- [ ] Neo4j inspection queries recorded in test artifacts for reproducibility.
- [ ] Dummy pipeline smoke test (`scripts/run_eval_dummy.sh`) documented with expected outputs.

## 7. Open Questions
- OQ1 Should the lookup table for dummy IDs live in configuration, or be derived from Neo4j at runtime?
- OQ2 What confidence thresholds should trigger the low-confidence fallback described in FR4?
- OQ3 Do production pipelines require similar slot rebalance safeguards, or is this strictly a dummy path issue?

## Implementation Status Snapshot

| FR  | Acceptance Criterion | Status | Notes |
| --- | -------------------- | ------ | ----- |
| FR1 | AC1                  | ✅ 구현 | `pipeline.analyze` 디버그 로그에 `norm_image_id`/`norm_image_id_source`가 일관되게 출력됨. |
| FR1 | AC2                  | ✅ 구현 | 더미 lookup 시 저장 URI가 레지스트리 경로와 일치하는지 통합 테스트로 검증함(`test_pipeline_persists_canonical_storage_uri_for_dummy_lookup`). |
| FR1 | AC3                  | ✅ 구현 | `tests/test_paths_and_analyze.py::test_pipeline_normalises_dummy_id_from_file_path`에서 중복 업서트 시 단일 노드 유지 확인. |
| FR2 | AC4                  | ✅ 구현 | 그래프 컨텍스트가 비어도 `force_dummy_fallback` 경로에서 시드 findings가 재주입됨. |
| FR2 | AC5                  | ✅ 구현 | 업서트 결과 `finding_ids`에 시드 ID가 포함되며, Neo4j 질의로 동일 ID 확인 가능. |
| FR2 | AC6                  | ✅ 구현 | 응답 및 디버그 메타데이터에 `finding_source="mock_seed"`와 `seeded_finding_ids`가 노출됨. |
| FR3 | AC7                  | ✅ 구현 | `_rebalance_slot_limits`와 대응 테스트로 리포트 슬롯이 최소 1개 확보됨. |
| FR3 | AC8                  | ✅ 구현 | `test_pipeline_auto_context_includes_described_by_path`가 리밸런싱 후 `DESCRIBED_BY` 경로 포함 여부를 검증. |
| FR3 | AC9                  | ✅ 구현 | `test_pipeline_report_override_parity_matches_auto`가 `k_reports=2` 오버라이드와 자동 리밸런스 결과가 동일함을 확인. |
| FR4 | AC10                 | ✅ 구현 | VGL이 그래프 근거를 확보하면 합의가 `status="agree"`로 승격됨(테스트 및 curl 검증). |
| FR4 | AC11                 | ✅ 구현 | 그래프 근거 부재 시 `status="low_confidence"` 및 원인 노트가 응답에 포함됨. |
| FR4 | AC12                 | ✅ 구현 | `_replace_image_tokens` 적용으로 플레이스홀더 `(IMAGE_ID)`가 최종 응답에서 제거됨. |
