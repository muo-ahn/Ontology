// GraphDB Modeling Seed (sample subset)
// Use LOAD CSV for full dataset; this script seeds sample nodes for demos/tests.

CREATE CONSTRAINT IF NOT EXISTS FOR (p:Patient) REQUIRE p.patient_id IS UNIQUE;
CREATE CONSTRAINT IF NOT EXISTS FOR (e:Encounter) REQUIRE e.encounter_id IS UNIQUE;
CREATE CONSTRAINT IF NOT EXISTS FOR (o:Observation) REQUIRE o.observation_id IS UNIQUE;
CREATE CONSTRAINT IF NOT EXISTS FOR (d:Diagnosis) REQUIRE d.diagnosis_id IS UNIQUE;
CREATE CONSTRAINT IF NOT EXISTS FOR (pr:Procedure) REQUIRE pr.procedure_id IS UNIQUE;
CREATE CONSTRAINT IF NOT EXISTS FOR (m:Medication) REQUIRE m.med_id IS UNIQUE;
CREATE CONSTRAINT IF NOT EXISTS FOR (img:Image) REQUIRE img.image_id IS UNIQUE;
CREATE CONSTRAINT IF NOT EXISTS FOR (ai:AIInference) REQUIRE ai.inference_id IS UNIQUE;
CREATE CONSTRAINT IF NOT EXISTS FOR (ov:OntologyVersion) REQUIRE ov.version_id IS UNIQUE;

MERGE (ov:OntologyVersion {version_id:'1.1'})
ON CREATE SET ov.applied_at = datetime(), ov.description = 'Sample seed for graph modeling revamp';

// Patients
WITH [
  {patient_id:'P1001', name:'Patient_1001', sex:'F', birth_date:'1975-10-27', region:'Seoul'},
  {patient_id:'P1002', name:'Patient_1002', sex:'M', birth_date:'2008-09-30', region:'Seoul'},
  {patient_id:'P1003', name:'Patient_1003', sex:'M', birth_date:'1985-11-21', region:'Daegu'},
  {patient_id:'P1004', name:'Patient_1004', sex:'F', birth_date:'1992-01-05', region:'Busan'},
  {patient_id:'P1005', name:'Patient_1005', sex:'F', birth_date:'1968-04-11', region:'Incheon'}
] AS rows
UNWIND rows AS r
MERGE (p:Patient {patient_id:r.patient_id})
SET p += {name:r.name, sex:r.sex, birth_date:r.birth_date, region:r.region};

// Encounters
WITH [
  {encounter_id:'E0001', patient_id:'P1001', encounter_type:'outpatient', start_at:'2025-07-22T00:00:00', end_at:'2025-07-22T00:00:00'},
  {encounter_id:'E0002', patient_id:'P1001', encounter_type:'outpatient', start_at:'2025-07-02T00:00:00', end_at:'2025-07-02T00:00:00'},
  {encounter_id:'E0003', patient_id:'P1002', encounter_type:'outpatient', start_at:'2025-06-30T00:00:00', end_at:'2025-07-01T00:00:00'},
  {encounter_id:'E0004', patient_id:'P1003', encounter_type:'outpatient', start_at:'2025-07-12T00:00:00', end_at:'2025-07-17T00:00:00'},
  {encounter_id:'E0005', patient_id:'P1003', encounter_type:'outpatient', start_at:'2025-06-24T00:00:00', end_at:'2025-06-25T00:00:00'},
  {encounter_id:'E0006', patient_id:'P1004', encounter_type:'inpatient', start_at:'2025-07-05T00:00:00', end_at:'2025-07-09T00:00:00'},
  {encounter_id:'E0008', patient_id:'P1005', encounter_type:'outpatient', start_at:'2025-07-15T00:00:00', end_at:'2025-07-15T12:00:00'}
] AS rows
UNWIND rows AS r
MERGE (e:Encounter {encounter_id:r.encounter_id})
SET e += {encounter_type:r.encounter_type, start_at:r.start_at, end_at:r.end_at}
WITH e, r
MATCH (p:Patient {patient_id:r.patient_id})
MERGE (p)-[:HAS_ENCOUNTER]->(e);

// Observations (subset)
WITH [
  {observation_id:'O00003', encounter_id:'E0005', loinc_code:'8462-4', name:'Diastolic BP', value:76.0, unit:'mmHg', observed_at:'2025-06-24T23:00:00'},
  {observation_id:'O00004', encounter_id:'E0003', loinc_code:'2093-3', name:'Cholesterol', value:156.0, unit:'mg/dL', observed_at:'2025-06-30T18:00:00'}
] AS rows
UNWIND rows AS r
MATCH (enc:Encounter {encounter_id:r.encounter_id})
MERGE (obs:Observation {observation_id:r.observation_id})
SET obs += {
  loinc_code:r.loinc_code,
  name:r.name,
  value:r.value,
  unit:r.unit,
  observed_at:r.observed_at
}
MERGE (enc)-[:HAS_OBSERVATION]->(obs);

// Imaging
WITH [
  {image_id:'IMG001', encounter_id:'E0003', modality:'XR', captured_at:'2025-06-30T10:10:00', storage_uri:'/mnt/data/medical_dummy/images/img_001.png', caption_hint:'Chest X-ray – probable right upper lobe nodule (~1.8 cm). Recommend CT follow-up.'},
  {image_id:'IMG002', encounter_id:'E0006', modality:'US', captured_at:'2025-07-06T09:45:00', storage_uri:'/mnt/data/medical_dummy/images/img_002.png', caption_hint:'Abdominal ultrasound – fatty liver pattern. No gallstones visualized.'},
  {image_id:'IMG003', encounter_id:'E0005', modality:'ECG', captured_at:'2025-06-24T11:35:00', storage_uri:'/mnt/data/medical_dummy/images/img_003.png', caption_hint:'ECG snapshot – sinus tachycardia (~110 bpm), no ST elevation.'}
] AS rows
UNWIND rows AS r
MATCH (enc:Encounter {encounter_id:r.encounter_id})
MERGE (img:Image {image_id:r.image_id})
SET img += {modality:r.modality, captured_at:r.captured_at, storage_uri:r.storage_uri, caption_hint:r.caption_hint}
MERGE (enc)-[:HAS_IMAGE]->(img);

// Procedures
WITH [
  {procedure_id:'PR0001', encounter_id:'E0003', cpt_code:'71045', description:'Chest radiograph single view', performed_at:'2025-06-30T10:15:00'},
  {procedure_id:'PR0002', encounter_id:'E0005', cpt_code:'93010', description:'Electrocardiogram interpretation', performed_at:'2025-06-24T11:40:00'},
  {procedure_id:'PR0003', encounter_id:'E0006', cpt_code:'74177', description:'CT abdomen with contrast', performed_at:'2025-07-05T14:20:00'}
] AS rows
UNWIND rows AS r
MATCH (enc:Encounter {encounter_id:r.encounter_id})
MERGE (pr:Procedure {procedure_id:r.procedure_id})
SET pr += {cpt_code:r.cpt_code, description:r.description, performed_at:r.performed_at}
MERGE (enc)-[:HAS_PROCEDURE]->(pr);

// Medications
WITH [
  {med_id:'M00001', encounter_id:'E0003', drug_name:'amlodipine', for_icd10:'I10', dose:'10mg', route:'oral', schedule:'qd', start_time:'2025-06-30T09:00:00', end_time:'2025-07-14T09:00:00'},
  {med_id:'M00002', encounter_id:'E0005', drug_name:'azithromycin', for_icd10:'J18.9', dose:'500mg', route:'oral', schedule:'qd', start_time:'2025-06-24T08:00:00', end_time:'2025-06-28T08:00:00'},
  {med_id:'M00003', encounter_id:'E0006', drug_name:'atorvastatin', for_icd10:'E78.5', dose:'20mg', route:'oral', schedule:'hs', start_time:'2025-07-05T22:00:00', end_time:'2025-07-19T22:00:00'},
  {med_id:'M00004', encounter_id:'E0008', drug_name:'metformin', for_icd10:'E11.9', dose:'850mg', route:'oral', schedule:'bid', start_time:'2025-07-15T07:00:00', end_time:'2025-08-15T07:00:00'}
] AS rows
UNWIND rows AS r
MATCH (enc:Encounter {encounter_id:r.encounter_id})
MERGE (m:Medication {med_id:r.med_id})
SET m += {
  drug_name:r.drug_name,
  for_icd10:r.for_icd10,
  dose:r.dose,
  route:r.route,
  schedule:r.schedule,
  start_time:r.start_time,
  end_time:r.end_time
}
MERGE (enc)-[:HAS_MEDICATION]->(m);

// AI Inferences with provenance & versioning
WITH [
  {inference_id:'AI00001', image_id:'IMG001', encounter_id:'E0003', model:'llava-1.5-7b', model_version:'v1.0', task:'caption', output:'Chest X-ray – probable right upper lobe nodule (~1.8 cm). Recommend CT follow-up.', confidence:0.91, timestamp:'2025-10-10T12:00:00', source_type:'observation', source_reference:'O00004', role:'vision'},
  {inference_id:'AI00002', image_id:'IMG002', encounter_id:'E0006', model:'llava-1.5-7b', model_version:'v1.0', task:'caption', output:'Abdominal ultrasound – fatty liver pattern. No gallstones visualized.', confidence:0.85, timestamp:'2025-10-10T12:00:00', source_type:'procedure', source_reference:'PR0003', role:'vision'},
  {inference_id:'AI00003', image_id:'IMG003', encounter_id:'E0005', model:'qwen2-vl:2b', model_version:'v0.9', task:'caption', output:'ECG snapshot – sinus tachycardia (~110 bpm), no ST elevation.', confidence:0.79, timestamp:'2025-10-10T12:00:00', source_type:'observation', source_reference:'O00003', role:'vision'}
] AS rows
UNWIND rows AS r
MATCH (enc:Encounter {encounter_id:r.encounter_id})
MATCH (img:Image {image_id:r.image_id})
MERGE (ai:AIInference {inference_id:r.inference_id})
SET ai += {
  model:r.model,
  model_version:r.model_version,
  task:r.task,
  output:r.output,
  confidence:r.confidence,
  timestamp:r.timestamp,
  source_type:r.source_type,
  source_reference:r.source_reference,
  version:'1.1'
}
MERGE (img)-[:HAS_INFERENCE {role:r.role}]->(ai)
MERGE (enc)-[:HAS_INFERENCE {role:'llm'}]->(ai)
MERGE (ai)-[:RECORDED_WITH]->(ov);

// Link inferences to source artifacts when available
MATCH (ai:AIInference {source_type:'observation'})-[:RECORDED_WITH]->(ov:OntologyVersion {version_id:'1.1'})
MATCH (obs:Observation {observation_id:ai.source_reference})
MERGE (ai)-[:DERIVES_FROM]->(obs);

MATCH (ai:AIInference {source_type:'procedure'})-[:RECORDED_WITH]->(:OntologyVersion {version_id:'1.1'})
MATCH (pr:Procedure {procedure_id:ai.source_reference})
MERGE (ai)-[:DERIVES_FROM]->(pr);
