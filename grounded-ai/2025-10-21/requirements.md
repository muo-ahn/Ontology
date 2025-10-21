# Mid-Fusion Multimodal Reasoning Requirements

## Functional
- Accept medical images plus textual prompts and optional patient/encounter identifiers, producing textual reports and structured graph updates through a mid-fusion LLM/VLM stack.
- Support both real-time (synchronous) and deferred (async) inference modes, exposing intermediate fusion activations and retrieved graph facts when requested.
- Persist image metadata, fusion configuration (e.g., injection layer, gating parameters), and resulting narratives/structured outputs with stable idempotency keys to prevent duplication.
- Provide deterministic fallbacks: if mid-fusion fails validation, the system must emit a late-fusion result while flagging the degradation.

## Fusion Architecture
- Maintain a shared embedding interface that projects VLM visual tokens into the LLM hidden dimension and supports configurable fusion depth (early, mid, late) at runtime.
- Store serialized visual token caches so repeat requests can skip VLM feature extraction where permitted.
- Expose instrumentation hooks (attention maps, layer outputs) for offline evaluation and debugging; ensure sampling controls (temperature, top-k) are logged alongside fusion parameters.

## Graph Integration
- Derive Cypher templates that map LLM-planned slots (patient, modality, suspected finding) to Neo4j queries, allowing retrieval prior to each decoding block.
- Enrich the decoding loop with retrieved graph facts by injecting them as gated memory tokens or key/value caches, tracking provenance and timestamps.
- Enforce Neo4j schema constraints (`Patient`, `Encounter`, `Image`, `AIInference`, `Finding`, etc.) and guarantee that new knowledge emitted by the LLM is reconciled against existing nodes before writes.
- Cache graph query results per `idempotency_key` and expire them on ontology updates to balance freshness vs. latency.

## Orchestration & APIs
- Keep the message-driven pipeline (Redis Streams or equivalent) but add explicit stages for `fusion.prepare`, `graph.retrieve`, and `fusion.decode`.
- Extend the public API to accept fusion mode hints, graph retrieval toggles, and explanation-level (raw facts vs. summarized).
- Provide SSE/WebSocket streams that surface step-level progress including graph retrieval outcomes and any fallbacks invoked.

## Reliability & Evaluation
- Implement automatic health checks that ensure Cypher calls stay within latency budgets and fall back to cached results on transient failures.
- Capture evaluation metrics comparing late-fusion, mid-fusion, and graph-augmented responses (accuracy, hallucination rate, readability) on a curated validation set.
- Maintain detailed structured logging (request id, fusion mode, graph queries executed, retry counts) and export metrics for dashboards/alerts.
- Cover the fusion layers, Cypher retrieval logic, and ontology upserts with unit, integration, and end-to-end tests; require deterministic seeds for reproducible comparisons.

## Developer Experience
- Supply Make targets or scripts for running mid-fusion demos, regenerating visual token caches, seeding Neo4j fixtures, and replaying event logs.
- Document the architecture (data flow diagrams, fusion configs, retrieval prompts) and provide quick-start guides for tuning fusion depth or Cypher templates.
- Ensure Docker Compose bundles all dependencies (Neo4j, Redis, worker pods, evaluation notebooks) with sample env files illustrating required secrets.
