# GraphDB Modeling Revamp Requirements

## Objectives
- Improve the medical ontology stored in Neo4j so it accurately reflects clinical workflows, supports multi-modal reasoning, and remains resilient to data quality issues.
- Enable downstream systems (mid-fusion inference, analytics, auditing) to rely on consistent graph structures and metadata.

## Functional Requirements
1. **Expanded Clinical Coverage**  
   - Model encounters, observations, diagnoses, procedures, medications, imaging, and AI inferences with explicit temporal relationships.  
   - Represent provenance for inferred knowledge (e.g., which model generated a node or relationship).
2. **Ontology Versioning**  
   - Track schema revisions and support co-existence of multiple ontology versions during migration periods.  
   - Provide an API/metadata field that indicates which version of the schema a node/relationship conforms to.
3. **Data Validation**  
   - Enforce uniqueness and existence constraints for core identifiers (patient_id, encounter_id, etc.).  
   - Validate clinical codes (ICD, LOINC, SNOMED) against reference vocabularies where feasible.

## Non-Functional Requirements
1. **Performance & Scalability**  
   - Keep query latency under 150 ms for common read patterns (patient timeline, encounter summary, inference lineage).  
   - Support incremental ingestion of new data without requiring full graph reloads.
2. **Maintainability**  
   - Provide clear migration scripts (up/down) and change logs for schema updates.  
   - Ensure new contributors can understand node/relationship semantics via documentation.
3. **Observability**  
   - Emit metrics/logs when constraint violations or ontology mismatches occur.  
   - Capture lineage metadata so downstream audits can trace data flow end-to-end.

## Tooling & Automation
1. **Schema Definition**  
   - Maintain Cypher-based schema definitions under version control.  
   - Provide a CLI or Make target to apply migrations idempotently.
2. **Testing Harness**  
   - Supply integration tests (pytest or similar) that spin up Neo4j, seed fixtures, run migrations, and validate graph invariants.  
   - Include property-based or snapshot tests for critical Cypher queries.
3. **Data Quality Checks**  
   - Implement automated checks (pre-ingest or post-ingest) that flag anomalies (e.g., orphan nodes, inconsistent encounter timelines).

## Documentation Requirements
- Update architecture diagrams to reflect revised entity/relationship model.  
- Produce onboarding notes describing how to extend the ontology safely.  
- Provide example queries for common use cases (timeline, cohort selection, inference provenance).
