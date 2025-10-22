// GraphDB Modeling Revamp – Constraints & Indexes (version 1.1)

// Patient
CREATE CONSTRAINT patient_id_unique IF NOT EXISTS
FOR (p:Patient) REQUIRE p.patient_id IS UNIQUE;

// Encounter
CREATE CONSTRAINT encounter_id_unique IF NOT EXISTS
FOR (e:Encounter) REQUIRE e.encounter_id IS UNIQUE;

CREATE INDEX encounter_start_idx IF NOT EXISTS
FOR (e:Encounter) ON (e.start_at);

// Observation
CREATE CONSTRAINT observation_id_unique IF NOT EXISTS
FOR (o:Observation) REQUIRE o.observation_id IS UNIQUE;

// Diagnosis
CREATE CONSTRAINT diagnosis_id_unique IF NOT EXISTS
FOR (d:Diagnosis) REQUIRE d.diagnosis_id IS UNIQUE;

// Procedure
CREATE CONSTRAINT procedure_id_unique IF NOT EXISTS
FOR (pr:Procedure) REQUIRE pr.procedure_id IS UNIQUE;

CREATE INDEX procedure_performed_idx IF NOT EXISTS
FOR (pr:Procedure) ON (pr.performed_at);

// Medication
CREATE CONSTRAINT medication_id_unique IF NOT EXISTS
FOR (m:Medication) REQUIRE m.med_id IS UNIQUE;

// Image
CREATE CONSTRAINT image_id_unique IF NOT EXISTS
FOR (img:Image) REQUIRE img.image_id IS UNIQUE;

CREATE INDEX image_captured_idx IF NOT EXISTS
FOR (img:Image) ON (img.captured_at);

// AIInference
CREATE CONSTRAINT inference_id_unique IF NOT EXISTS
FOR (ai:AIInference) REQUIRE ai.inference_id IS UNIQUE;

// OntologyVersion
CREATE CONSTRAINT ontology_version_id_unique IF NOT EXISTS
FOR (ov:OntologyVersion) REQUIRE ov.version_id IS UNIQUE;
