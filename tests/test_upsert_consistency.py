import os

import pytest

from grounded_ai.api.services.graph_repo import GraphRepo


pytestmark = pytest.mark.integration


def _neo4j_configured() -> bool:
    return bool(os.getenv("NEO4J_URI"))


@pytest.mark.skipif(not _neo4j_configured(), reason="NEO4J_URI is not configured for integration test")
def test_upsert_returns_and_verifies_finding_ids():
    repo = GraphRepo.from_env()
    payload = {
        "case_id": "CASE_IMG_001",
        "image": {
            "image_id": "IMG_001",
            "modality": "CT",
            "storage_uri": "/mnt/data/medical_dummy/images/img_001.png",
            "path": "/data/medical_dummy/images/api_test_data/Acute-fatty-liver-of-pregnancy-non-contrast-computed-tomography-Non-contrast-computed.png",
        },
        "report": {
            "id": "RPT-12345",
            "text": "CT scan of the abdomen showing a hypodense mass in the right lobe of the liver.",
            "model": "Radiologist",
            "conf": 0.8,
        },
        "findings": [
            {
                "id": "FIND-12345",
                "type": "Mass",
                "location": "Right lobe of the liver",
                "conf": 0.9,
                "size_cm": 5.0,
            }
        ],
    }
    try:
        receipt = repo.upsert_case(payload)
        assert receipt.get("finding_ids"), "upsert_case must return persisted finding_ids"
        verified = repo.fetch_finding_ids(receipt["image_id"], receipt["finding_ids"])
        assert set(verified) == set(receipt["finding_ids"]), "re-query must match upsert receipt"
    finally:
        repo.close()
