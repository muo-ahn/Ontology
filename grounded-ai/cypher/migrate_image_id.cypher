// Migration: promote Image.id to Image.image_id
// 1. Ensure the new property is populated for legacy nodes
MATCH (img:Image)
SET img.image_id = coalesce(img.image_id, img.id)
REMOVE img.id;

// 2. Drop legacy constraint if present and enforce uniqueness on image_id
DROP CONSTRAINT img_id IF EXISTS;
CREATE CONSTRAINT img_image_id IF NOT EXISTS FOR (img:Image) REQUIRE img.image_id IS UNIQUE;
