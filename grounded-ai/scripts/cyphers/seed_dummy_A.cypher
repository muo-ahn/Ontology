// ---- Common constraints (idempotent) ----
CREATE CONSTRAINT IF NOT EXISTS FOR (img:Image) REQUIRE img.image_id IS UNIQUE;
CREATE CONSTRAINT IF NOT EXISTS FOR (fd:Finding) REQUIRE fd.id IS UNIQUE;
CREATE CONSTRAINT IF NOT EXISTS FOR (an:Anatomy) REQUIRE an.code IS UNIQUE;
CREATE CONSTRAINT IF NOT EXISTS FOR (rep:Report) REQUIRE rep.id IS UNIQUE;
CREATE CONSTRAINT IF NOT EXISTS FOR (ov:OntologyVersion) REQUIRE ov.version_id IS UNIQUE;

MERGE (ov:OntologyVersion {version_id:'1.1'})
ON CREATE SET ov.applied_at = datetime(), ov.description = 'Dummy seed for edge-density experiment';

// ---- Dummy A (≈10 nodes, HIGH edge density) ----
WITH [
  {image_id:'A_IMG_001', modality:'CT', storage_uri:'/data/dummy/A_IMG_001.png'},
  {image_id:'A_IMG_002', modality:'US', storage_uri:'/data/dummy/A_IMG_002.png'},
  {image_id:'A_IMG_003', modality:'XR', storage_uri:'/data/dummy/A_IMG_003.png'}
] AS imgs
UNWIND imgs AS r
MERGE (i:Image {image_id:r.image_id})
SET i.modality=r.modality, i.storage_uri=r.storage_uri;

WITH [
  {id:'A_F_001', type:'mass', location:'right lobe', size_cm:3.5, conf:0.9},
  {id:'A_F_002', type:'nodule', location:'RUL', size_cm:1.6, conf:0.85},
  {id:'A_F_003', type:'ischemic', location:'MCA', size_cm:2.1, conf:0.8},
  {id:'A_F_004', type:'steatosis', location:'liver', size_cm:0.0, conf:0.92}
] AS fds
UNWIND fds AS f
MERGE (fd:Finding {id:f.id})
SET fd += {type:f.type, location:f.location, size_cm:toFloat(f.size_cm), conf:toFloat(f.conf)};

WITH [
  {code:'AN_LIVER', name:'Liver'},
  {code:'AN_BRAIN', name:'Brain'},
  {code:'AN_RUL',   name:'Right Upper Lobe'}
] AS anatomies
UNWIND anatomies AS a
MERGE (an:Anatomy {code:a.code})
SET an.name=a.name;

WITH [
  {id:'A_R_001', text:'Hepatic steatosis with focal mass suspicion.', conf:0.88},
  {id:'A_R_002', text:'Right upper lobe pulmonary nodule.', conf:0.82},
  {id:'A_R_003', text:'Ischemic change in MCA territory.', conf:0.86}
] AS reps
UNWIND reps AS r
MERGE (rep:Report {id:r.id})
SET rep.text=r.text, rep.conf=toFloat(r.conf);

MATCH (i1:Image {image_id:'A_IMG_001'}),
      (i2:Image {image_id:'A_IMG_002'}),
      (i3:Image {image_id:'A_IMG_003'}),
      (f1:Finding {id:'A_F_001'}),
      (f2:Finding {id:'A_F_002'}),
      (f3:Finding {id:'A_F_003'}),
      (f4:Finding {id:'A_F_004'}),
      (anL:Anatomy {code:'AN_LIVER'}),
      (anB:Anatomy {code:'AN_BRAIN'}),
      (anR:Anatomy {code:'AN_RUL'}),
      (r1:Report {id:'A_R_001'}),
      (r2:Report {id:'A_R_002'}),
      (r3:Report {id:'A_R_003'})
MERGE (i1)-[:HAS_FINDING]->(f1)
MERGE (i1)-[:HAS_FINDING]->(f4)
MERGE (i1)-[:DESCRIBED_BY]->(r1)
MERGE (f1)-[:LOCATED_IN]->(anL)
MERGE (f4)-[:LOCATED_IN]->(anL)
MERGE (i2)-[:HAS_FINDING]->(f2)
MERGE (i2)-[:DESCRIBED_BY]->(r2)
MERGE (f2)-[:LOCATED_IN]->(anR)
MERGE (i3)-[:HAS_FINDING]->(f3)
MERGE (i3)-[:DESCRIBED_BY]->(r3)
MERGE (f3)-[:LOCATED_IN]->(anB)

// Cross links (edge density ↑)
MERGE (i2)-[:DESCRIBED_BY]->(r1)
MERGE (i1)-[:DESCRIBED_BY]->(r2)
MERGE (f2)-[:RELATED_TO {kind:'ddx'}]->(f1)
MERGE (f3)-[:RELATED_TO {kind:'systemic'}]->(f1);
