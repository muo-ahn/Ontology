
// Minimal seed using sample rows (for demo). For full import, place CSVs into Neo4j import and use LOAD CSV.
CREATE CONSTRAINT IF NOT EXISTS FOR (p:Patient) REQUIRE p.patient_id IS UNIQUE;
CREATE CONSTRAINT IF NOT EXISTS FOR (e:Encounter) REQUIRE e.encounter_id IS UNIQUE;
CREATE CONSTRAINT IF NOT EXISTS FOR (o:Observation) REQUIRE o.observation_id IS UNIQUE;
CREATE CONSTRAINT IF NOT EXISTS FOR (d:Diagnosis) REQUIRE d.diagnosis_id IS UNIQUE;
CREATE CONSTRAINT IF NOT EXISTS FOR (m:Medication) REQUIRE m.med_id IS UNIQUE;
CREATE CONSTRAINT IF NOT EXISTS FOR (i:Image) REQUIRE i.image_id IS UNIQUE;
CREATE CONSTRAINT IF NOT EXISTS FOR (a:AIInference) REQUIRE a.inference_id IS UNIQUE;

// Patients (first 3)
WITH [
{patient_id:'P1001', name:'Patient_1001', sex:'F', birth_date:'1975-10-27', region:'Seoul'},
{patient_id:'P1002', name:'Patient_1002', sex:'M', birth_date:'2008-09-30', region:'Seoul'},
{patient_id:'P1003', name:'Patient_1003', sex:'M', birth_date:'1985-11-21', region:'Daegu'}
] AS rows
UNWIND rows AS r
MERGE (p:Patient {patient_id:r.patient_id})
SET p += {name:r.name, sex:r.sex, birth_date:r.birth_date, region:r.region};

// Encounters (first 5)
WITH [
{encounter_id:'E0001', patient_id:'P1001', encounter_type:'outpatient', start_time:'2025-07-22T00:00:00', end_time:'2025-07-22T00:00:00'},
{encounter_id:'E0002', patient_id:'P1001', encounter_type:'outpatient', start_time:'2025-07-02T00:00:00', end_time:'2025-07-02T00:00:00'},
{encounter_id:'E0003', patient_id:'P1002', encounter_type:'outpatient', start_time:'2025-06-30T00:00:00', end_time:'2025-07-01T00:00:00'},
{encounter_id:'E0004', patient_id:'P1003', encounter_type:'outpatient', start_time:'2025-07-12T00:00:00', end_time:'2025-07-17T00:00:00'},
{encounter_id:'E0005', patient_id:'P1003', encounter_type:'outpatient', start_time:'2025-06-24T00:00:00', end_time:'2025-06-25T00:00:00'}
] AS rows
UNWIND rows AS r
MERGE (e:Encounter {encounter_id:r.encounter_id})
SET e += {encounter_type:r.encounter_type, start_time:r.start_time, end_time:r.end_time}
WITH e, r
MATCH (p:Patient {patient_id:r.patient_id})
MERGE (p)-[:HAS_ENCOUNTER]->(e);

// Imaging (all)
WITH [
{image_id:'IMG001', patient_id:'P1002', encounter_id:'E0009', modality:'XR', file_path:'/mnt/data/medical_dummy/images/img_001.png', caption_hint:'Chest X-ray – probable right upper lobe nodule (~1.8 cm). Recommend CT follow-up.'},
{image_id:'IMG002', patient_id:'P1005', encounter_id:'E0006', modality:'ECG', file_path:'/mnt/data/medical_dummy/images/img_002.png', caption_hint:'Abdominal ultrasound – fatty liver pattern. No gallstones visualized.'},
{image_id:'IMG003', patient_id:'P1001', encounter_id:'E0005', modality:'XR', file_path:'/mnt/data/medical_dummy/images/img_003.png', caption_hint:'ECG snapshot – sinus tachycardia (~110 bpm), no ST elevation.'}
] AS rows
UNWIND rows AS r
MATCH (p:Patient {patient_id:r.patient_id})
MATCH (e:Encounter {encounter_id:r.encounter_id})
MERGE (img:Image {image_id:r.image_id})
SET img += {modality:r.modality, file_path:r.file_path, caption_hint:r.caption_hint}
MERGE (e)-[:HAS_IMAGE]->(img);

// AI Inference (all)
WITH [
{inference_id:'AI00001', image_id:'IMG001', model:'llava-1.5-7b', task:'caption', output:'Chest X-ray – probable right upper lobe nodule (~1.8 cm). Recommend CT follow-up.', confidence:'0.91', timestamp:'2025-10-10T12:00:00'},
{inference_id:'AI00002', image_id:'IMG002', model:'llava-1.5-7b', task:'caption', output:'Abdominal ultrasound – fatty liver pattern. No gallstones visualized.', confidence:'0.85', timestamp:'2025-10-10T12:00:00'},
{inference_id:'AI00003', image_id:'IMG003', model:'qwen2-vl:2b', task:'caption', output:'ECG snapshot – sinus tachycardia (~110 bpm), no ST elevation.', confidence:'0.79', timestamp:'2025-10-10T12:00:00'}
] AS rows
UNWIND rows AS r
MATCH (img:Image {image_id:r.image_id})
MERGE (a:AIInference {inference_id:r.inference_id})
SET a += {model:r.model, task:r.task, output:r.output, confidence:r.confidence, timestamp:r.timestamp}
MERGE (img)-[:HAS_INFERENCE]->(a);
