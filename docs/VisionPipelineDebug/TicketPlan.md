# Vision Pipeline Debug — Ticket Plan

This plan decomposes the confirmed issues (S01–S07) from `docs/VisionPipelineDebug/ConfirmedProblemScope.md` into actionable tickets. Each ticket lists current symptoms, scope/goal boundaries, concrete tasks, validation strategy, and dependencies.

---

## S01 — Slot Limit Telemetry Matches Actual Context

- **Problem & Evidence**: Debug runs (e.g., IMG201) report `context_slot_limits.findings = 0` even when `context_findings_len > 0`. Metadata misleads downstream tooling (`ConfirmedProblemScope.md §S01`).
- **Scope / Goal**:
  - Bind slot-limit reporting to the allocator’s real output inside `api/services/context_pack.py` (`_allocate_slots`, `_rebalance_slots`).
  - Ensure serialized response mirrors allocator state; no divergence allowed between telemetry and payload.
- **Key Tasks**:
  1. Audit slot allocation flow to locate overwrite/zeroing bug.
  2. Add guard: if findings exist, enforce `slot_limits['findings'] >= 1` (respecting global cap `k_default`).
  3. Update serializer/debug summary to reference finalized slot map.
  4. Unit test: fabricate context with ≥1 finding and assert slot telemetry ≥1.
- **Validation**:
  - `./scripts/vision_pipeline_debug.sh IMG201` → `context_slot_limits.findings` equals actual `context_findings_len` (clamped to cap).
  - New pytest covering slot allocator logic.
- **Dependencies**: none, but pairs well with S02 (both touch `context_pack`).

---

## S02 — Evidence Paths Surface When Edges Exist

- **Problem & Evidence**: Edge summaries show relations, yet `context_paths_len` stays 0 and `[EVIDENCE PATHS]` block prints "데이터 없음" (`ConfirmedProblemScope.md §S02`).
- **Scope / Goal**:
  - Update graph query (`GraphRepo.query_paths`) to `MATCH (p=...)` and return actual path records.
  - Convert returned triples into top-k paths via `_build_context_paths` and serialize into debug output.
- **Key Tasks**:
  1. Modify Cypher query to collect ordered paths plus metadata (confidence, relation types).
  2. Update repo + service layers to expect `paths` key instead of `hits` only.
  3. Implement deterministic sorting + truncation for top-k presentation.
  4. Add explicit "No path generated (0/k)" string only when zero edges exist.
  5. Integration test with seeded mini-graph verifying `context_paths_len ≥ 1` when edges present.
- **Validation**:
  - Run IMG201 and inspect `context_paths_head` (non-empty) and `[EVIDENCE PATHS]` list in `triples` text.
  - CI test invoking mock graph ensures serialization.
- **Dependencies**: touches same files as S01; coordinate to avoid merge conflicts.

---

## S03 — Upsert Failure Downgrades Response Instead of Hiding Data

- **Problem & Evidence**: IMG_001 shows `normalized_findings_len > 0` but `finding_ids` empty, yet final response tells user "데이터가 없습니다" (`ConfirmedProblemScope.md §S03`).
- **Scope / Goal**:
  - Detect `normalized_findings` + missing IDs, mark run as `status=degraded`, and communicate reason while surfacing fallback facts.
  - Ensure errors propagate to consensus layer and logs.
- **Key Tasks**:
  1. In `api/services/upsert_repo.py`, add guard raising structured error/flag when IDs missing.
  2. Propagate degraded flag through `pipeline.py` response contract.
  3. Provide fallback facts (normalized findings) in `graph_context.facts.findings` even without graph IDs.
  4. Extend integration test (IMG_001 fixture) expecting degraded status + explanatory note.
  5. Update documentation/README to mention degraded pathway.
- **Validation**:
  - `vision_pipeline_debug.sh IMG_001` shows `status=degraded`, `notes="graph upsert failed, fallback used"`, and non-empty findings.
  - Error log emitted once per run.
- **Dependencies**: S04 relies on consistent provenance once fallback triggered.

---

## S04 — Provenance Metadata Is Consistent and Accurate

- **Problem & Evidence**: `finding_source`, `seeded_finding_ids`, and `finding_fallback.*` fields remain null or contradict actual generation mode (`ConfirmedProblemScope.md §S04`).
- **Scope / Goal**:
  - Introduce centralized provenance object shared across normalization, fallback, and response assembly so metadata reflects real pipeline decisions.
- **Key Tasks**:
  1. Define `FindingProvenance` dataclass (fields: source enum, seeded_ids, fallback metadata, forced flag).
  2. Populate object immediately after normalization (or dummy registry hit) and pass through context builder.
  3. Ensure fallback activations (manual or automatic) update the same object.
  4. Update serializers/debug output to read from provenance object only.
  5. Add deterministic test verifying repeated runs for same image yield identical provenance values.
- **Validation**:
  - IMG201 repeated 3× → identical `finding_source`, `seeded_finding_ids`, `finding_fallback` fields.
  - Force flag reflects actual CLI parameter (see S05).
- **Dependencies**: depends on S05 to make `force_dummy_fallback` reachable; interacts with S06 deterministic seeding.

---

## S05 — `force_dummy_fallback` Contract Works End-to-End

- **Problem & Evidence**: `vision_pipeline_debug.sh` sends malformed JSON (unescaped braces) causing decode errors; fallback never forced (`ConfirmedProblemScope.md §S05`).
- **Scope / Goal**:
  - Fix CLI to produce valid JSON bodies; FastAPI endpoint must parse optional bool and drive fallback branch.
- **Key Tasks**:
  1. Update `scripts/vision_pipeline_debug.sh` to build request body via `jq` (merge file path + additional params argument).
  2. Document CLI usage (`README`, script header) with examples covering `--params '{"force_dummy_fallback":true}'`.
  3. Adjust `routers/pipeline.py` request model to include `force_dummy_fallback: Optional[bool] = False` and plumb through to fallback controller.
  4. Ensure provenance object (S04) records `finding_fallback.forced=true` when flag asserted and marks fallback strategy.
  5. Add regression shell test (e.g., new target in `scripts/test_pipeline_integrity.sh`) that calls the script with `--params` and greps for `"finding_fallback":{"forced":true` in the response log.
- **Validation**:
  - CLI invocation no longer throws JSON decode error.
  - Debug output shows fallback path engaged and forced flag true.
- **Dependencies**: touches same CLI as verification plan; interacts with S04 metadata.

---

## S06 — Deterministic Debug Snapshots Per Image

- **Problem & Evidence**: Same `image_id` (IMG201) yields different `pre_upsert_findings_head` across runs, making logs non-reproducible (`ConfirmedProblemScope.md §S06`).
- **Scope / Goal**:
  - Seed all randomness (dummy registry selections, ordering, mock scores) with `image_id` (or provided seed) so repeated runs stabilize.
- **Key Tasks**:
  1. Introduce helper (e.g., `set_deterministic_seed(image_id: str)`) invoked at pipeline entry to seed `random`, `numpy.random`, and any other RNGs.
  2. Audit modules using randomness (dummy registry, normalization heuristics, similarity ranking) and ensure they derive from deterministic RNG.
  3. Enforce deterministic sorting for context sections that currently rely on dictionary order.
  4. Add test executing same case thrice and asserting identical `pre_upsert_findings_head` plus provenance block.
  5. Document deterministic behavior (and how to override seed) in README / debug docs.
- **Validation**:
  - `vision_pipeline_debug.sh IMG201` run three times → byte-identical normalization/provenance portions.
- **Dependencies**: S04 depends on deterministic provenance; S07 requires stable inputs for fair scoring.

---

## S07 — Consensus Module Produces Meaningful Agreements

- **Problem & Evidence**: All runs emit `status=disagree`, `confidence=low`, `agreement_score≈0`, making ensemble output unusable (`ConfirmedProblemScope.md §S07`).
- **Scope / Goal**:
  - Redesign consensus scoring to blend text similarity, type/location overlap, and graph evidence; allow thresholds that yield non-trivial agreements when signals align.
- **Key Tasks**:
  1. Analyze `services/evaluation.py` to understand current zeroed scores; document limitations.
  2. Implement weighted scoring: textual cosine similarity, ontology type match, anatomical overlap, plus bonus if evidence paths (S02) support agreement.
  3. Introduce configurable thresholds (e.g., `status="agree" if score > 0.35`, `confidence="medium" if score > 0.55`) and expose them via settings.
  4. Update consensus notes to explain why a decision was agree/disagree, referencing graph evidence IDs when available.
  5. Build regression fixtures spanning agree, borderline, and disagree scenarios to keep distribution healthy.
- **Validation**:
  - At least one canonical test case results in `status=agree`, `confidence>=medium`.
  - Agreement scores cover (0,1] across regression suite instead of collapsing to zero.
- **Dependencies**: Benefits from S02 (paths) and S06 (determinism) to provide richer, stable inputs.

---

## Execution Notes

1. **Ordering**: Prioritize S01–S05 (telemetry & interface contract) to unblock meaningful signals, then tackle S06/S07 which assume deterministic, instrumented data.
2. **Testing**: Convert the verification checklist in `docs/VisionPipelineDebug/ExpectedBehavior.md` into automated CI (pytest + shell harness) so each ticket adds guards.
3. **Documentation**: After each ticket, refresh `docs/VisionPipelineDebug/*.md` and `README` sections to reflect new behaviors and CLI usage.
