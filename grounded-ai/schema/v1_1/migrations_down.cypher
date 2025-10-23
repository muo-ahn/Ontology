// GraphDB Modeling Revamp – Migration Down (version 1.1 rollback)

// 1. Detach OntologyVersion relationships
MATCH (:AIInference)-[rel:RECORDED_WITH]->(:OntologyVersion {version_id: '1.1'})
DELETE rel;

// 2. Remove version property set by migration
MATCH (inference:AIInference)
WHERE inference.version = '1.1'
REMOVE inference.version;

// 3. Restore encounters whose end_at was populated by migration
MATCH (enc:Encounter)
WHERE enc._end_at_was_null = true
SET enc.end_at = null
REMOVE enc._end_at_was_null;

// 4. Delete OntologyVersion node
MATCH (v:OntologyVersion {version_id: '1.1'})
DETACH DELETE v;
