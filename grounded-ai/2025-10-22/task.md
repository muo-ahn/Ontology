# GraphDB Modeling Execution Plan

## 1. Preparation
- Spin up local Neo4j instance (Docker or existing Compose stack).  
- Ensure test suite dependencies are available: `pytest`, `neo4j` Python driver, Docker CLI.  
- Create working branch `feature/graphdb-modeling-2025-10-22`.

## 2. Task Breakdown

### Task A – Schema Authoring
- Draft Cypher files for constraints/indexes (`schema/v1_1/constraints.cypher`).  
- Define node/relationship creation scripts (`schema/v1_1/migrations_up.cypher`).  
- Add rollback script (`schema/v1_1/migrations_down.cypher`).  
- Deliverable: Schema directory with versioned scripts.

### Task B – Seed Data & Fixtures
- Update CSV fixtures to include procedures, extended medication fields, AI inference provenance columns.  
- Revise `seed.cypher` to load new nodes/relationships and insert `OntologyVersion`.  
- Verify import by running full seed against local Neo4j.

### Task C – Ingestion Service Update
- Modify Python services/workers that write to Neo4j:  
  - Populate provenance edges and version references.  
  - Ensure idempotent merges align with new constraints.  
- Add logging for constraint violation handling.

### Task D – Validation & Tests
- Create pytest integration suite that:  
  - Spins up Neo4j via Docker.  
  - Applies migrations.  
  - Loads sample fixtures.  
  - Asserts key invariants (no orphan nodes, unique identifiers, timeline ordering).  
- Add smoke queries in tests for encounter timeline and inference provenance.

### Task E – Documentation
- Update architecture diagram (draw.io/mermaid) reflecting new nodes/relationships.  
- Draft onboarding notes and example Cypher queries.  
- Document migration workflow (CLI/Make usage, validation steps).

## 3. Timeline (Suggested)
- **Day 1**: Tasks A & B (schema + fixtures).  
- **Day 2**: Task C (service updates) and begin Task D tests.  
- **Day 3**: Complete Task D, execute full validation, finish Task E documentation.  
- **Buffer**: Additional day for review and adjustments.

## 4. Exit Criteria
- Migrations run cleanly (up/down) on fresh and existing databases.  
- Integration tests pass in CI.  
- Documentation published and linked from project README.  
- Stakeholders review and sign off on updated ontology structure.
