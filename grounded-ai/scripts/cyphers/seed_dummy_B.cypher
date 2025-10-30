// ---- Common constraints (idempotent) ----
CREATE CONSTRAINT IF NOT EXISTS FOR (img:Image) REQUIRE img.image_id IS UNIQUE;
CREATE CONSTRAINT IF NOT EXISTS FOR (fd:Finding) REQUIRE fd.id IS UNIQUE;
CREATE CONSTRAINT IF NOT EXISTS FOR (an:Anatomy) REQUIRE an.code IS UNIQUE;
CREATE CONSTRAINT IF NOT EXISTS FOR (rep:Report) REQUIRE rep.id IS UNIQUE;
CREATE CONSTRAINT IF NOT EXISTS FOR (ov:OntologyVersion) REQUIRE ov.version_id IS UNIQUE;

MERGE (ov:OntologyVersion {version_id:'1.1'})
ON CREATE SET ov.applied_at = datetime(), ov.description = 'Dummy seed for edge-density experiment';

// ---- Dummy B (≈50 nodes, LOW edge density) ----
WITH range(1,25) AS idx
UNWIND idx AS k
WITH k, 100 + k AS base
WITH k, right('000'+toString(base), 3) AS suffix
MERGE (i:Image {image_id:('IMG'+suffix)})
SET i.modality = CASE WHEN k % 3 = 0 THEN 'CT' WHEN k % 3 = 1 THEN 'US' ELSE 'XR' END,
    i.storage_uri = '/data/dummy/IMG'+suffix+'.png';

WITH range(1,25) AS idx
UNWIND idx AS k
WITH k, 100 + k AS base
WITH k, right('000'+toString(base), 3) AS suffix
MERGE (fd:Finding {id:('F'+suffix)})
SET fd.type = CASE WHEN k % 3 = 0 THEN 'mass' WHEN k % 3 = 1 THEN 'nodule' ELSE 'ischemic' END,
    fd.location = CASE WHEN k % 2 = 0 THEN 'liver' ELSE 'lung' END,
    fd.size_cm = toFloat( round((rand()*4.0)+0.5,1) ),
    fd.conf = 0.5 + rand()*0.2;

// Sparse: only 1-to-1 minimal edges
WITH range(1,25) AS idx
UNWIND idx AS k
WITH k, right('000'+toString(100 + k), 3) AS suffix
MATCH (i:Image {image_id:('IMG'+suffix)}),
      (fd:Finding {id:('F'+suffix)})
MERGE (i)-[:HAS_FINDING]->(fd);
