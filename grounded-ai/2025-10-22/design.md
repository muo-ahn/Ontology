# GraphDB Modeling Design – 2025-10-22

## 1. Scope
- Refine the Neo4j ontology supporting the multimodal reasoning pipeline.  
- Focus on schema normalization, versioning, and data quality safeguards without disrupting existing ingestion flows.  
- Deliver design assets that developers can implement in staged migrations.

## 2. Current Pain Points
1. **Loose Relationships**: AIInference nodes may lack explicit provenance or encounter context.  
2. **Temporal Ambiguity**: Observations, diagnoses, and imaging events do not consistently encode time ranges.  
3. **Schemas Drift Manually**: Schema adjustments are ad hoc; no structured migration log.  
4. **Data Integrity Risks**: Missing constraints allow orphan nodes and duplicate identifiers.

## 3. Target Ontology Shape
### 3.1 Core Entities
| Label | Key Properties | Notes |
|-------|----------------|-------|
| `Patient` | `patient_id`, demographics | Unique per patient |
| `Encounter` | `encounter_id`, `start_at`, `end_at`, `type` | Linked to `Patient` |
| `Observation` | `observation_id`, `loinc_code`, `value`, `unit`, `observed_at` | Optional `reference_range` |
| `Diagnosis` | `diagnosis_id`, `icd_code`, `confidence`, `recorded_at` | Distinguish clinical vs inferred |
| `Procedure` | `procedure_id`, `cpt_code`, `performed_at` | New node to capture interventions |
| `Medication` | `med_id`, `drug_name`, `dose`, `route`, `schedule` | |
| `Image` | `image_id`, `modality`, `captured_at`, `storage_uri` | |
| `AIInference` | `inference_id`, `model`, `task`, `timestamp`, `version` | Link to inputs and outputs |
| `OntologyVersion` | `version_id`, `applied_at`, `description` | Maintains schema lineage |

### 3.2 Relationships
- `(Patient)-[:HAS_ENCOUNTER]->(Encounter)`  
- `(Encounter)-[:HAS_OBSERVATION]->(Observation)`  
- `(Encounter)-[:HAS_DIAGNOSIS]->(Diagnosis)`  
- `(Encounter)-[:HAS_PROCEDURE]->(Procedure)`  
- `(Encounter)-[:HAS_MEDICATION]->(Medication)`  
- `(Encounter)-[:HAS_IMAGE]->(Image)`  
- `(Image)-[:HAS_INFERENCE {role:'vision'}]->(AIInference)`  
- `(Encounter)-[:HAS_INFERENCE {role:'llm'}]->(AIInference)`  
- `(AIInference)-[:DERIVES_FROM]->(Observation|Diagnosis|Procedure|Image)` (provenance)  
- `(AIInference)-[:RECORDED_WITH]->(OntologyVersion)`  
- `(Observation|Diagnosis|Procedure)-[:VERIFIED_BY]->(AIInference)` (optional feedback loop)

### 3.3 Temporal Modeling
- Store `*_at` or `*_start/_end` timestamps on nodes.  
- Use `VALID_DURING` relationships for events spanning intervals (e.g., medication regimen).  
- Introduce `EncounterTimeline` virtual view (cypher query template) to assemble chronological events.

## 4. Versioning & Migration Strategy
1. **Schema Registry**  
   - Maintain `/schema/vX_Y/constraints.cypher` and `/schema/vX_Y/migrations.cypher`.  
   - Each migration declares prerequisites and writes an entry to `OntologyVersion`.
2. **Dual-Write Window**  
   - During rollout, ingestion services write to both old and new structures (when feasible) with version tags.  
3. **Validation Phase**  
   - Run consistency checks comparing old vs new representations before removing legacy edges.  
4. **Rollback Plan**  
   - Provide `migrations_down.cypher` scripts to revert to previous schema if validation fails.

## 5. Data Quality Controls
1. **Constraints**  
   - Uniqueness on all primary IDs; existence constraints on critical properties.  
   - Custom constraint: `MATCH (i:AIInference) WHERE i.model IS NULL RETURN i` should yield zero rows post-ingestion.
2. **Automated Checks**  
   - Cypher recipes to detect orphan nodes, conflicting timelines, missing provenance.  
   - Integration tests executed via CI (Dockerized Neo4j).
3. **Metadata Logging**  
   - Each ingestion batch records success/failure counts, constraint violations, and writes to `IngestionAudit` node.

## 6. Implementation Plan (High-Level)
1. **Schema Authoring** – Draft Cypher for new nodes/relationships, constraints, indexes.  
2. **Fixture Update** – Extend seed data (CSV + `seed.cypher`) to align with new schema.  
3. **Migration Scripts** – Create up/down scripts with version IDs.  
4. **Ingestion Refactor** – Update Python services/workers to populate new relationships and provenance data.  
5. **Validation Suite** – Write pytest integration suite to assert constraints and sample queries.  
6. **Documentation** – Produce diagrams and onboarding notes.

## 7. Open Questions
- Should AI inference provenance include model hyperparameters for lineage?  
- How to reconcile late-arriving clinical facts with already committed AI inferences (conflict resolution)?  
- What retention policy is needed for historical ontology versions?  
- Do we need multi-tenancy (support for multiple facilities) in this phase?
