// ---- Migration: Promote image_id + align AI inference version tracking ----

// Ensure every Image node uses image_id and drop legacy id property.
MATCH (img:Image)
SET img.image_id = coalesce(img.image_id, img.id)
REMOVE img.id;

// Correct modality metadata for IMG_002 (confirmed data-quality issue).
MERGE (img_fix:Image {image_id:'IMG_002'})
SET img_fix.modality = 'US',
    img_fix.storage_uri = coalesce(img_fix.storage_uri, '/mnt/data/medical_dummy/images/img_002.png'),
    img_fix.caption_hint = coalesce(
        img_fix.caption_hint,
        'Abdominal ultrasound – fatty liver pattern. No gallstones visualized.'
    );

// Create/ensure OntologyVersion nodes and align AIInference.version_id fields.
MERGE (default_version:OntologyVersion {version_id:'1.1'})
ON CREATE SET
    default_version.applied_at = datetime(),
    default_version.description = 'Ontology spec-driven seed v1.1';

MATCH (ai:AIInference)
SET ai.version_id = coalesce(ai.version_id, ai.version, '1.1')
REMOVE ai.version
WITH ai
MERGE (ov:OntologyVersion {version_id: ai.version_id})
ON CREATE SET ov.applied_at = datetime()
MERGE (ai)-[:RECORDED_WITH]->(ov);
