# Action Plan for Vision→LLM→Ontology Revamp

## Phase 1 – Graph Upsert & API Enhancements
- Extend `/vision/inference` payload to accept new image metadata (`modality`, optional `patient_id`/`encounter_id`) and generate deterministic IDs when absent.
- Refactor Neo4j writes to use idempotent `MERGE` patterns that create `Image`, optional `Patient/Encounter` links, and `AIInference` nodes in a single transaction.
- Add idempotency markers (e.g., `Idempotency` node keyed by SHA256) to short-circuit duplicate processing.
- Bump request/worker timeouts and add structured logging (already started) to capture parameters and resulting IDs.

## Phase 2 – Event-Driven Pipeline
- Introduce Redis Streams via Docker Compose; create topics `image.received`, `vision.captions`, `nlp.interpretation`, `graph.upserted`, plus optional dead-letter queues.
- Split the current synchronous endpoint into:
  1. **Ingress handler**: saves file, computes idempotency key, publishes `image.received`, returns 202 + stream token.
  2. **Background workers** (async tasks/Celery) listening on Redis to run VLM, LLM, and graph upserts sequentially.
- Implement an SSE endpoint (`/events/{key}`) that streams step updates pulled from Redis.
- Update Streamlit UI to upload images, subscribe to SSE, and display step-by-step progression.

## Phase 3 – Persistence & Retrieval Improvements
- Automatically compute and store embeddings (Qdrant) during the pipeline, storing vector identifiers on both `Image` and `AIInference`.
- When metadata (patient/encounter) arrives late, publish “link-intent” events that merge the appropriate relationships without duplicating nodes.
- Add optional LLM post-processing to suggest graph enrichment tasks (e.g., add conditions, recommend tests).

## Phase 4 – Reliability, Testing, and Docs
- Implement retry/backoff with capped attempts; send failures to dead-letter streams for manual inspection.
- Capture metrics (latency per stage, success/failure counts) and expose via logs or simple dashboard.
- Add unit/integration tests for worker logic, Neo4j upserts, and idempotency behavior.
- Document full setup in README (broker, workers, SSE usage) and provide Make targets (`make workers`, `make replay`, etc.) to ease local development.
