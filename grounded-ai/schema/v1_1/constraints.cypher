// Constraint set for Ontology schema v1.1

CREATE CONSTRAINT patient_id IF NOT EXISTS
FOR (p:Patient) REQUIRE p.patient_id IS UNIQUE;

CREATE CONSTRAINT encounter_id IF NOT EXISTS
FOR (e:Encounter) REQUIRE e.encounter_id IS UNIQUE;

CREATE CONSTRAINT observation_id IF NOT EXISTS
FOR (o:Observation) REQUIRE o.observation_id IS UNIQUE;

CREATE CONSTRAINT diagnosis_id IF NOT EXISTS
FOR (d:Diagnosis) REQUIRE d.diagnosis_id IS UNIQUE;

CREATE CONSTRAINT procedure_id IF NOT EXISTS
FOR (pr:Procedure) REQUIRE pr.procedure_id IS UNIQUE;

CREATE CONSTRAINT medication_id IF NOT EXISTS
FOR (m:Medication) REQUIRE m.med_id IS UNIQUE;

CREATE CONSTRAINT finding_id IF NOT EXISTS
FOR (f:Finding) REQUIRE f.id IS UNIQUE;

CREATE CONSTRAINT report_id IF NOT EXISTS
FOR (r:Report) REQUIRE r.id IS UNIQUE;

CREATE CONSTRAINT anatomy_code IF NOT EXISTS
FOR (a:Anatomy) REQUIRE a.code IS UNIQUE;

CREATE CONSTRAINT image_image_id IF NOT EXISTS
FOR (img:Image) REQUIRE img.image_id IS UNIQUE;

CREATE CONSTRAINT ai_inference_id IF NOT EXISTS
FOR (ai:AIInference) REQUIRE ai.inference_id IS UNIQUE;

CREATE CONSTRAINT ontology_version_id IF NOT EXISTS
FOR (ov:OntologyVersion) REQUIRE ov.version_id IS UNIQUE;
