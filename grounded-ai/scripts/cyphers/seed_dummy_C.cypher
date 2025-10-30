// ---- Common constraints (idempotent) ----
CREATE CONSTRAINT IF NOT EXISTS FOR (img:Image) REQUIRE img.image_id IS UNIQUE;
CREATE CONSTRAINT IF NOT EXISTS FOR (fd:Finding) REQUIRE fd.id IS UNIQUE;
CREATE CONSTRAINT IF NOT EXISTS FOR (an:Anatomy) REQUIRE an.code IS UNIQUE;
CREATE CONSTRAINT IF NOT EXISTS FOR (rep:Report) REQUIRE rep.id IS UNIQUE;
CREATE CONSTRAINT IF NOT EXISTS FOR (ov:OntologyVersion) REQUIRE ov.version_id IS UNIQUE;

MERGE (ov:OntologyVersion {version_id:'1.1'})
ON CREATE SET ov.applied_at = datetime(), ov.description = 'Dummy seed for edge-density experiment';

// ---- Dummy C (≈50 nodes, HIGH edge density) ----
WITH range(1,25) AS idx
UNWIND idx AS k
MERGE (i:Image {image_id:('C_IMG_'+toString(k))})
SET i.modality = CASE WHEN k % 3 = 0 THEN 'CT' WHEN k % 3 = 1 THEN 'US' ELSE 'XR' END,
    i.storage_uri = '/data/dummy/C_IMG_'+toString(k)+'.png';

WITH range(1,25) AS idx
UNWIND idx AS k
MERGE (fd:Finding {id:('C_F_'+toString(k))})
SET fd.type = CASE WHEN k % 3 = 0 THEN 'mass' WHEN k % 3 = 1 THEN 'nodule' ELSE 'ischemic' END,
    fd.location = CASE WHEN k % 2 = 0 THEN 'liver' ELSE 'lung' END,
    fd.size_cm = toFloat( round((rand()*4.0)+0.5,1) ),
    fd.conf = 0.7 + rand()*0.25;

WITH [
  {code:'AN_LIVER', name:'Liver'},
  {code:'AN_LUNG', name:'Lung'},
  {code:'AN_BRAIN', name:'Brain'}
] AS anatomies
UNWIND anatomies AS a
MERGE (an:Anatomy {code:a.code}) SET an.name=a.name;

WITH range(1,10) AS idx
UNWIND idx AS k
MERGE (rep:Report {id:('C_R_'+toString(k))})
SET rep.text = CASE WHEN k % 2 = 0 THEN 'Findings suggest focal hepatic lesion.' ELSE 'Pulmonary nodule noted.' END,
    rep.conf = 0.75 + rand()*0.2;

// Dense edges: each image connects to multiple findings, reports, anatomy
WITH range(1,25) AS idx
UNWIND idx AS k
MATCH (i:Image {image_id:('C_IMG_'+toString(k))}),
      (f1:Finding {id:('C_F_'+toString(k))}),
      (f2:Finding {id:('C_F_'+toString( (k % 25) + 1 ))}),
      (anL:Anatomy {code:'AN_LIVER'}),
      (anLu:Anatomy {code:'AN_LUNG'})
WITH i, f1, f2, anL, anLu, k,
     CASE WHEN k % 2 = 0 THEN anL ELSE anLu END AS loc1,
     CASE WHEN k % 2 = 1 THEN anL ELSE anLu END AS loc2
MERGE (i)-[:HAS_FINDING]->(f1)
MERGE (i)-[:HAS_FINDING]->(f2)
MERGE (f1)-[:LOCATED_IN]->(loc1)
MERGE (f2)-[:LOCATED_IN]->(loc2)
WITH i, f1, f2
MATCH (rep:Report) WITH i, f1, f2, rep ORDER BY rand() LIMIT 1
MERGE (i)-[:DESCRIBED_BY]->(rep)
MERGE (f1)-[:RELATED_TO {kind:'co-occur'}]->(f2);

// Extra cross edges to increase path alternatives
WITH range(1,12) AS idx
UNWIND idx AS k
MATCH (fA:Finding {id:('C_F_'+toString(k))}),
      (fB:Finding {id:('C_F_'+toString(k+12))})
MERGE (fA)-[:RELATED_TO {kind:'ddx'}]->(fB);
