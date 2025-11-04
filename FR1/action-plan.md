# FR1 Action Plan

## Goal & Scope
- Deliver FR1 requirements from `docs/spec-dummy-pipeline.md`: align dummy requests with seeded Neo4j `Image` nodes, honour explicit IDs, avoid persisting synthetic identifiers.
- Limit changes to the dummy evaluation path (`run_eval_dummy.sh`, `/pipeline/analyze`) and supporting services/utilities.

## Workstream A – Dummy Registry Module
- Implement `DummyImageRegistry` next to `services/dummy_dataset.py`.
- Load `grounded-ai/data/medical_dummy/imaging.csv` (plus alias metadata) and cache lookup maps by canonical ID and filename using `lru_cache`.
- Provide helpers:
  - `normalise_id(raw_id: str) -> str`
  - `resolve(path: str) -> LookupResult | None`
- Acceptance: registry resolves primary filenames, API test filenames, and explicit IDs to `IMG20X`; exposes seeded `storage_uri`.

## Workstream B – Pipeline Integration
- Update `_derive_image_id_from_path` (`grounded-ai/api/routers/pipeline.py`) to call the registry before the alphanumeric fallback and return `(image_id, storage_uri, source)`.
- In the normalization flow:
  - Continue to prioritise `payload.image_id`, but normalise it through the registry when present.
  - Apply lookup results to override VLM metadata (canonical ID, seeded URI, `image_id_source="dummy_lookup"`).
  - Ensure downstream graph upsert payloads, logs, and similarity inputs use the canonical ID; emit a debug warning if a synthetic ID slips through.
- Acceptance: debug blob includes `norm_image_id`, `norm_image_id_source`, `dummy_lookup_hit`; repeated dummy requests reuse the seeded node.

## Workstream C – Storage URI Handling
- Allow `_resolve_seed_storage_uri` to accept the registry-provided URI and skip synthetic fallbacks when one exists.
- Guarantee persisted/storage URIs stay within `/data/dummy/` or `/mnt/data/medical_dummy/images/`.
- Acceptance: running the dummy pipeline twice does not create new `Image` nodes; Neo4j inspection shows only seeded URIs (AC2/AC3).

## Workstream D – Instrumentation & Logging
- Emit structured log `pipeline.normalize.image_id` with case ID, canonical ID, lookup source, storage URI, and lookup hit boolean.
- Extend the debug payload with lookup diagnostics and add an optional flag when fallback logic is used.
- Acceptance: `scripts/run_eval_dummy.sh debug=1` surfaces seeded ID in `debug.norm_image_id` and indicates lookup path (AC1).

## Workstream E – Testing
- Unit tests:
  - Registry resolution cases (canonical filename, alias filename, explicit ID, miss).
  - `_derive_image_id_from_path` precedence of registry over fallback.
- Integration test:
  - Execute dummy pipeline twice; assert canonical ID in debug output, single `Image` node in Neo4j, seeded storage URI retained.
- Incorporate tests into `pytest`; document manual Neo4j query for validation.

## Timeline / Sequencing
1. Build and test the registry module (Workstream A).
2. Integrate registry into pipeline and URI handling (Workstreams B & C).
3. Add instrumentation/logging updates (Workstream D).
4. Implement unit/integration tests and run dummy smoke (Workstream E).
5. Update documentation/checklists once acceptance criteria verified.

## Risks & Mitigations
- **Stale alias metadata**: maintain alias list alongside CSV; consider Neo4j-derived hydration in follow-up.
- **Unexpected filenames**: retain fallback logic but emit debug alerts to flag unmapped variants early.
- **Test brittleness**: use fixtures/mocks for unit coverage; isolate Neo4j-dependent checks to integration layer with controlled data.
