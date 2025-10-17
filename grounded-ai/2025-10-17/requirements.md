# Vision–LLM–Ontology Pipeline Requirements

## Functional
- Support both existing and newly uploaded medical images. When an image ID is missing, the system must create the `Image` node (with optional patient/encounter links) before attaching inference results.
- Persist vision (VLM) and reasoning (LLM) outputs as `AIInference` nodes linked to their originating image, with idempotent handling so repeated requests do not duplicate graph entries.
- Expose an API endpoint that accepts image files plus prompts, returns intermediate/final outputs, and can operate asynchronously so the UI can stream progress.
- Maintain embeddings in Qdrant for retrieval; embed text and image payloads consistently whenever new inferences are created.

## Data & Graph
- Enforce Neo4j constraints on `Patient`, `Encounter`, `Image`, `AIInference`, etc., and ensure write paths use `MERGE` for safe upserts.
- Allow partial metadata (e.g., missing patient/encounter) and defer relationship creation until data becomes available, while keeping a record of pending links.
- Capture inference metadata: model name, task, temperature, timestamp, confidence, and a stable idempotency key (e.g., SHA256 of payload).

## Orchestration
- Transition from purely synchronous request/response to an event-driven workflow where VLM, LLM, and graph updates are decoupled workers.
- Introduce a local-friendly message broker (Redis Streams) with well-defined topics (`image.received`, `vision.captions`, `nlp.interpretation`, `graph.upserted`) and enforce consumer groups for scalability.
- Provide SSE/WebSocket endpoints so the UI can subscribe to per-request progress updates.

## Reliability & Observability
- Track processing state per `idempotency_key` with status updates (queued → captioned → interpreted → persisted).
- Record metrics such as step latency, failure counts, and retry attempts; expose logs that include key identifiers for traceability.
- Implement retry/backoff with dead-letter queues for events that fail repeatedly.

## Tooling & Dev Experience
- Update local Docker Compose to include Redis and optional background worker containers.
- Supply scripts or Make targets to seed Neo4j, start workers, and replay events.
- Document the pipeline, data contracts, and testing strategy to aid future contributors.
