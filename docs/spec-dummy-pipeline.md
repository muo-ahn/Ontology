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
  - AC2 `MATCH (i:Image) RETURN i.image_id, i.storage_uri ORDER BY i.created_at DESC LIMIT 3` shows storage URIs in `/data/dummy/` and no new synthetic IDs after a dummy run.
  - AC3 Upserting the same dummy image twice does not create additional `Image` nodes or alter the lookup mapping.

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
