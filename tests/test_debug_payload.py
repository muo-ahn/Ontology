from __future__ import annotations

from services.debug_payload import DebugPayloadBuilder


def _builder(enabled: bool = True) -> DebugPayloadBuilder:
    return DebugPayloadBuilder(enabled=enabled)


def test_builder_records_identity_fields():
    builder = _builder()
    builder.record_identity(
        normalized_image={"image_id": "IMG001", "path": "/tmp/a.png", "modality": "CT"},
        image_id="IMG001",
        image_id_source="dummy_lookup",
        storage_uri="/data/IMG001.png",
        lookup_hit=True,
        lookup_source="alias",
        warn_on_lookup_miss=False,
        fallback_meta={"used": False, "forced": False},
        finding_source="vlm",
        seeded_finding_ids=["F1"],
        provenance={"finding_source": "vlm"},
        pre_upsert_findings=[{"id": "F1", "type": "mass"}],
        report_confidence=0.9,
    )
    payload = builder.payload()
    assert payload["norm_image_id"] == "IMG001"
    assert payload["dummy_lookup_hit"] is True
    assert payload["finding_source"] == "vlm"
    assert payload["pre_upsert_findings_len"] == 1
    assert payload["pre_upsert_report_conf"] == 0.9


def test_builder_context_marks_graph_degrade():
    builder = _builder()
    builder.record_context(
        context_bundle={"summary": ["[EDGE SUMMARY]"], "slot_limits": {"findings": 1}},
        findings=[{"id": "F1"}],
        paths=[{"slot": "findings", "triples": ["A"]}],
        total_triples=1,
        graph_paths_strength=0.5,
        similar_seed_images=[{"id": "IMG201", "score": 1.0}],
        similarity_edges_created=0,
        similarity_threshold=0.5,
        similarity_candidates_considered=10,
        graph_degraded=True,
        fallback_used=True,
        fallback_reason="no_graph_paths",
        no_graph_evidence=True,
    )
    payload = builder.payload()
    assert payload["graph_degraded"] is True
    assert payload["context_paths_len"] == 1
    assert payload["graph_paths_strength"] == 0.5
    assert payload["context_fallback_used"] is True
    assert payload["context_fallback_reason"] == "no_graph_paths"
    assert payload["context_no_graph_evidence"] is True


def test_builder_disabled_no_payload():
    builder = _builder(enabled=False)
    builder.set_stage("context")
    builder.record_consensus({"text": "foo"})
    assert builder.payload() == {}
