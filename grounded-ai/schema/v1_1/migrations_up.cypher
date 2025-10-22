// GraphDB Modeling Revamp – Migration Up (version 1.1)

// 1. Record ontology version
MERGE (v:OntologyVersion {version_id: '1.1'})
ON CREATE SET
  v.applied_at = datetime(),
  v.description = 'GraphDB modeling revamp (procedures, provenance, versioning)';
;

// 2. Ensure AIInference nodes capture version metadata
MATCH (inf:AIInference)
SET inf.version = CASE
  WHEN inf.version IS NULL THEN '1.1'
  ELSE toString(inf.version)
END;

// 3. Link AIInference nodes to OntologyVersion for provenance
MATCH (inf:AIInference)
MATCH (v:OntologyVersion {version_id: '1.1'})
MERGE (inf)-[:RECORDED_WITH]->(v);
;

// 4. Normalize encounter timestamps (ensure end_at exists)
MATCH (enc:Encounter)
WHERE enc.end_at IS NULL AND enc.start_at IS NOT NULL
SET enc.end_at = enc.start_at,
    enc._end_at_was_null = true;
;
