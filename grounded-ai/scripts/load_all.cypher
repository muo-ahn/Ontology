CREATE CONSTRAINT patient_id IF NOT EXISTS FOR (n:Patient)    REQUIRE n.patient_id     IS UNIQUE;
CREATE CONSTRAINT enc_id     IF NOT EXISTS FOR (n:Encounter)  REQUIRE n.encounter_id   IS UNIQUE;
CREATE CONSTRAINT obs_id     IF NOT EXISTS FOR (n:Observation)REQUIRE n.observation_id IS UNIQUE;
CREATE CONSTRAINT dx_id      IF NOT EXISTS FOR (n:Diagnosis)  REQUIRE n.diagnosis_id   IS UNIQUE;
CREATE CONSTRAINT rx_id      IF NOT EXISTS FOR (n:Medication) REQUIRE n.med_id         IS UNIQUE;
CREATE CONSTRAINT img_id IF NOT EXISTS FOR (n:Image)      REQUIRE n.id       IS UNIQUE;
CREATE CONSTRAINT ai_id      IF NOT EXISTS FOR (n:AIInference)REQUIRE n.inference_id   IS UNIQUE;

LOAD CSV WITH HEADERS FROM 'file:///patients.csv' AS r
MERGE (p:Patient {patient_id: r.patient_id})
SET p.name = r.name, p.sex = r.sex, p.birth_date = r.birth_date, p.region = r.region;

LOAD CSV WITH HEADERS FROM 'file:///encounters.csv' AS r
MERGE (e:Encounter {encounter_id: r.encounter_id})
SET e.encounter_type = r.encounter_type, e.start_time = r.start_time, e.end_time = r.end_time
WITH r, e
MATCH (p:Patient {patient_id: r.patient_id})
MERGE (p)-[:HAS_ENCOUNTER]->(e);

LOAD CSV WITH HEADERS FROM 'file:///observations.csv' AS r
MERGE (o:Observation {observation_id: r.observation_id})
SET o.loinc_code = r.loinc_code, o.name = r.name, o.value = toFloat(r.value), o.unit = r.unit, o.timestamp = r.timestamp
WITH r, o
MATCH (e:Encounter {encounter_id: r.encounter_id})
MERGE (e)-[:HAS_OBS]->(o);

LOAD CSV WITH HEADERS FROM 'file:///diagnoses.csv' AS r
MERGE (d:Diagnosis {diagnosis_id: r.diagnosis_id})
SET d.icd10 = r.icd10, d.description = r.description
WITH r, d
MATCH (e:Encounter {encounter_id: r.encounter_id})
MERGE (e)-[:HAS_DX]->(d);

LOAD CSV WITH HEADERS FROM 'file:///medications.csv' AS r
MERGE (m:Medication {med_id: r.med_id})
SET m.drug_name = r.drug_name, m.for_icd10 = r.for_icd10, m.dose = r.dose, m.schedule = r.schedule
WITH r, m
MATCH (e:Encounter {encounter_id: r.encounter_id})
MERGE (e)-[:HAS_RX]->(m);

LOAD CSV WITH HEADERS FROM 'file:///imaging.csv' AS r
MERGE (i:Image {id: r.id})
SET i.modality = r.modality, i.file_path = r.file_path, i.caption_hint = r.caption_hint
WITH r, i
MATCH (e:Encounter {encounter_id: r.encounter_id})
MERGE (e)-[:HAS_IMAGE]->(i);

LOAD CSV WITH HEADERS FROM 'file:///ai_inference.csv' AS r
MERGE (a:AIInference {inference_id: r.inference_id})
SET a.model = r.model, a.task = r.task, a.output = r.output, a.confidence = toFloat(r.confidence), a.timestamp = r.timestamp
WITH r, a
MATCH (i:Image {id: r.id})
MERGE (i)-[h:HAS_INFERENCE]->(a)
ON CREATE SET h.confidence = toFloat(r.confidence), h.at = r.timestamp;
