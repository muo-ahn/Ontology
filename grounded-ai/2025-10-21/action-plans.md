# Action Plan for Mid-Fusion Multimodal Reasoning

## Phase 1 – Alignment & Data Contracts
- Map the shared latent space expectations for the LLM encoder and VLM backbone; define tensor shapes, normalization, and token budgets required for mid-fusion.
- Refresh dataset ingestion scripts so images arrive with modality, optional patient/encounter identifiers, and prior graph context keys used for retrieval.
- Update API schemas (FastAPI + Streamlit UI) to carry both raw image references and precomputed visual tokens when available.
- Harden Neo4j idempotent upserts (`MERGE` patterns, SHA256 keys) to guarantee graph consistency before fusion changes land.

## Phase 2 – Mid-Fusion Architecture Prototype
- Implement a shared projection layer that embeds VLM visual tokens into the LLM’s hidden dimension, enabling concatenation/attention at configurable layers.
- Expose a configuration switch to compare late-fusion vs. mid-fusion in the inference service, logging attention maps and intermediate activations for analysis.
- Extend the inference worker to batch-process image + prompt pairs, route them through the mid-fusion stack, and return both textual and structured outputs.
- Add regression tests ensuring mid-fusion responses stay deterministic for fixed seeds and that the fallback late-fusion path continues to work.

## Phase 3 – Graph-Integrated Reasoning Loop
- Design a Cypher query builder that, given the current LLM hidden state (or decoded plan tokens), issues targeted Neo4j lookups for related patients, encounters, or prior inferences.
- Implement a retrieval controller that injects graph facts back into the LLM mid-fusion sequence (as key/value memories or gated tokens) while tracking provenance.
- Cache retrieved subgraphs alongside idempotency keys so repeated queries for the same context re-use results and reduce graph load.
- Update the ontology writer to reconcile LLM suggestions with retrieved graph facts, emitting upsert events when discrepancies are detected.

## Phase 4 – Evaluation, Observability & Rollout
- Build evaluation harnesses that compare late-fusion vs. mid-fusion vs. graph-augmented outputs on curated medical scenarios, scoring correctness and hallucination rates.
- Instrument the pipeline with step-level metrics (fusion latency, Cypher execution time, graph cache hit rate) and surface them via logs/Dashboards.
- Expand CI to cover unit tests for fusion layers, integration tests for Cypher retrieval, and smoke tests for the end-to-end API.
- Refresh documentation (README, architecture diagrams, developer playbooks) and provide Make targets for running mid-fusion demos and graph-enabled inference.
