// ---- Rollback: undo version linkage while keeping data intact ----

// Move version_id back to version for legacy compatibility.
MATCH (ai:AIInference)
SET ai.version = coalesce(ai.version, ai.version_id)
REMOVE ai.version_id;

// Drop RECORDED_WITH relationships prior to deleting OntologyVersion nodes.
MATCH ()-[rel:RECORDED_WITH]->(:OntologyVersion)
DELETE rel;

// Remove OntologyVersion nodes introduced by the migration.
MATCH (ov:OntologyVersion)
DETACH DELETE ov;

// Recreate legacy img.id property for systems that still expect it.
MATCH (img:Image)
SET img.id = coalesce(img.id, img.image_id);
