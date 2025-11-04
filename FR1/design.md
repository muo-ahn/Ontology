# FR1 – Dummy Pipeline Image Identifier Normalisation

## Objectives
- Map every dummy dataset request to the seeded `Image` nodes so the analyzer operates on canonical graph entities.
- Respect explicitly supplied `image_id` values while still normalising their format to match Neo4j constraints.
- Prevent any synthetic identifiers from being persisted or logged as authoritative values for dummy inputs.

## Key Design Elements

### 1. Centralised Dummy Lookup
- Parse `grounded-ai/data/medical_dummy/imaging.csv` (plus alias metadata) into an in-memory registry keyed by canonical `image_id` and by source filename.
- Normalise lookups using `Path(path).name.lower()` and heuristic trimming (remove hash suffixes / variant infixes) so `/mnt/.../img_001.png` and long API test filenames match the same record.
- Expose the registry via a `DummyImageRegistry` helper co-located with `services/dummy_dataset.py`; cache results using `functools.lru_cache` for low overhead.

### 2. Enhanced `_derive_image_id_from_path`
- Before falling back to alphanumeric stems, consult `DummyImageRegistry.resolve(path)` and return the seeded `image_id` when available.
- Return a small dataclass (e.g. `LookupResult`) capturing `image_id`, `storage_uri`, and `source="dummy_lookup"` so callers can branch on the provenance.
- Preserve existing fallback logic for non-dummy inputs; cap synthetic IDs to `[:48]` characters as today.

### 3. Pipeline Threading
- In `pipeline.py`, continue to prioritise `payload.image_id` (R1.2) but normalise with `DummyImageRegistry.normalise_id` to accept variants like `img201`.
- When the lookup hits, override the VLM-provided image metadata with the seeded `image_id` and `storage_uri`, and tag `image_id_source="dummy_lookup"`.
- Only invoke `_resolve_seed_storage_uri` when the lookup lacks a URI, ensuring we never persist synthetic paths (R1.3).
- Store the canonical ID in every downstream structure: graph upsert payloads, debug blobs, log events, similarity edges.

### 4. Observability
- Extend debug payloads with `norm_image_id_source` and `dummy_lookup_hit` booleans; log `pipeline.normalize.image_id` events with the same fields to satisfy AC1 introspection.
- Add a lightweight assertion in debug mode warning when `norm_image_id` still matches the old synthetic hex pattern.

### 5. Testing Strategy
- Unit tests for `DummyImageRegistry` ensuring filename/alias/explicit-ID scenarios resolve correctly.
- Unit tests for `_derive_image_id_from_path` verifying lookup precedence and fallback behaviour.
- Integration test that runs the dummy pipeline twice against an image, asserting:
  - debug log shows `norm_image_id="IMG20X"` (AC1);
  - graph contains a single `Image` node with seeded `storage_uri` (AC2);
  - repetition does not create duplicates or mutate the registry (AC3).

## Open Questions & Next Steps
1. Should alias metadata live alongside `imaging.csv` or inside Neo4j so non-code owners can update mappings (ties to spec OQ1)?
2. Do we want runtime hydration from Neo4j to avoid stale CSV data if the seed changes?
3. After FR1 lands, evaluate extending the registry to cover FR2 fallback findings to keep configuration in one place.

