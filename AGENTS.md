# Repository Guidelines

## Project Structure & Module Organization
- Root workspace holds `grounded-ai/` (primary code and services), `docs/` (design notes), `scripts/` (automation helpers), and `tests/` (repo-level regression coverage).
- `grounded-ai/api/`: FastAPI service (`routers/` for endpoints, `services/` for graph/LLM orchestration, `models/` for Pydantic types).
- `grounded-ai/ui/`: Streamlit front end; use it when validating UX flows.
- `grounded-ai/cypher/` and `grounded-ai/scripts/cyphers/`: Neo4j schema/seed scripts; changes here require matching data migrations.
- `grounded-ai/tests/` contains unit/integration suites; `tests/` at repo root holds snapshot-style regressions for the current pipeline behavior.

## Build, Test, and Development Commands
- `cd grounded-ai && make up`: Build and start FastAPI, Neo4j, Qdrant, and UI containers. Use `make down` to stop.
- `make seed`: Push the sample ontology into Neo4j (requires the stack running). `make migrate-image-id` and `make migrate-constraints` are available for schema alignment.
- `bash grounded-ai/scripts/run_eval_dummy.sh A 2 120 <path>`: Run a smoke evaluation against dummy set A with top-k and max-chars controls.
- `pytest`: Run the full test suite; `pytest grounded-ai/tests/unit` or `pytest tests/test_context_orchestrator.py` narrow scope while iterating.
- `make verify-pipeline-debug`: cURL-based sanity check for `/pipeline/analyze` debug payloads.

## Coding Style & Naming Conventions
- Python 3.10+ with 4-space indentation and type hints; keep functions small and log with module-level `logger` instances.
- Keep graph identifiers explicit (`image_id`, `case_id`, `report.id`) and prefer snake_case for Python variables and filenames.
- Co-locate Cypher strings and their validation helpers; when changing property names, update Cypher, Pydantic models, and seeds together.
- Prefer pure functions in `services/` where feasible; avoid hidden globals and pass dependencies explicitly.

## Testing Guidelines
- Use `pytest` with markers for scope (`unit`, `integration`) and keep fixtures in `conftest.py` beside their suite when possible.
- Add regression cases under `tests/` for pipeline/debug payload compatibility and under `grounded-ai/tests/unit/` for algorithmic pieces (similarity, fallback).
- For graph changes, include a Neo4j seed or migration snippet and assert both happy-path and missing-field behavior.
- Keep tests deterministic: fix random seeds, avoid network calls, and use the medical dummy dataset under `grounded-ai/data/medical_dummy/` for inputs.

## Commit & Pull Request Guidelines
- Write imperative, focused commits (e.g., `api: align image_id constraint`, `cypher: add finding->anatomy path`); split schema migrations and app code when possible.
- PRs should include: a concise summary, affected endpoints/scripts, test evidence (`pytest ...` output or screenshots for UI), and linked issues/spec IDs.
- Call out backward-incompatible graph changes, new environment variables, or model downloads required for reviewers.

## Security & Configuration Notes
- Do not commit secrets; use env vars for Neo4j (`NEO4J_USER`, `NEO4J_PASS`) and Ollama hosts. `.env` files stay local.
- When touching containers, ensure `docker compose` changes remain compatible with both CPU and GPU profiles (`make up` vs `make gpu`).
