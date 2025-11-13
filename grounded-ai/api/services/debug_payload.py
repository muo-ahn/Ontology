"""
Helper utilities for assembling debug payloads exposed via /pipeline/analyze.

Keeps router logic focused on orchestration while ensuring consistent debug keys.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional


class DebugPayloadBuilder:
    """Mutable helper that no-ops when debug logging is disabled."""

    def __init__(self, enabled: bool, *, initial_stage: str = "init") -> None:
        self.enabled = enabled
        self._payload: Dict[str, Any] = {"stage": initial_stage} if enabled else {}

    @staticmethod
    def _safe_value(value: Any) -> Any:
        if isinstance(value, (str, int, float, bool)) or value is None:
            return value
        if isinstance(value, list):
            return [DebugPayloadBuilder._safe_value(item) for item in value]
        if isinstance(value, tuple):
            return [DebugPayloadBuilder._safe_value(item) for item in value]
        if isinstance(value, dict):
            return {str(k): DebugPayloadBuilder._safe_value(v) for k, v in value.items()}
        return str(value)

    def set_stage(self, stage: str) -> None:
        if not self.enabled:
            return
        self._payload["stage"] = stage

    def record_identity(
        self,
        *,
        normalized_image: Dict[str, Any],
        image_id: str,
        image_id_source: str,
        storage_uri: Optional[str],
        lookup_hit: bool,
        lookup_source: Optional[str],
        warn_on_lookup_miss: bool,
        fallback_meta: Dict[str, Any],
        finding_source: Optional[str],
        seeded_finding_ids: Iterable[str],
        provenance: Dict[str, Any],
        pre_upsert_findings: List[Dict[str, Any]],
        report_confidence: Optional[float],
    ) -> None:
        if not self.enabled:
            return
        self._payload["stage"] = "pre_upsert"
        self._payload["normalized_image"] = {
            "image_id": normalized_image.get("image_id"),
            "path": normalized_image.get("path"),
            "modality": normalized_image.get("modality"),
        }
        self._payload["norm_image_id"] = image_id
        self._payload["norm_image_id_source"] = image_id_source
        if storage_uri:
            self._payload["storage_uri"] = storage_uri
        self._payload["dummy_lookup_hit"] = lookup_hit
        if lookup_source:
            self._payload["dummy_lookup_source"] = lookup_source
        if warn_on_lookup_miss:
            self._payload["norm_image_id_warning"] = "dummy_lookup_miss"
        fallback_payload = dict(fallback_meta)
        if seeded_finding_ids:
            fallback_payload.setdefault("seeded_ids_head", list(seeded_finding_ids)[:3])
        self._payload["finding_fallback"] = fallback_payload
        if finding_source:
            self._payload["finding_source"] = finding_source
        self._payload["seeded_finding_ids"] = list(seeded_finding_ids)
        self._payload["finding_provenance"] = dict(provenance)
        self._payload["pre_upsert_findings_len"] = len(pre_upsert_findings)
        self._payload["pre_upsert_findings_head"] = pre_upsert_findings[:2]
        self._payload["pre_upsert_report_conf"] = report_confidence

    def record_upsert(
        self,
        receipt: Dict[str, Any],
        finding_ids: Iterable[str],
        *,
        verified_ids: Optional[Iterable[str]] = None,
    ) -> None:
        if not self.enabled:
            return
        payload = {
            "stage": "post_upsert",
            "upsert_receipt": dict(receipt),
            "post_upsert_finding_ids": list(finding_ids),
        }
        if verified_ids is not None:
            payload["post_upsert_verified_ids"] = list(verified_ids)
        self._payload.update(payload)

    def record_upsert_payload(
        self,
        *,
        raw_payload: Dict[str, Any],
        prepared_payload: Dict[str, Any],
    ) -> None:
        if not self.enabled:
            return
        self._payload["upsert_payload"] = {
            "raw": self._safe_value(raw_payload),
            "prepared": self._safe_value(prepared_payload),
        }

    def record_context(
        self,
        *,
        context_bundle: Dict[str, Any],
        findings: List[Dict[str, Any]],
        paths: List[Dict[str, Any]],
        total_triples: int,
        graph_paths_strength: float,
        similar_seed_images: List[Dict[str, Any]],
        similarity_edges_created: int,
        similarity_threshold: Optional[float],
        similarity_candidates_considered: int,
        graph_degraded: bool,
        context_consistency: Optional[bool] = None,
        context_consistency_reason: Optional[str] = None,
    ) -> None:
        if not self.enabled:
            return
        self._payload.update({
            "stage": "context",
            "context_summary": context_bundle.get("summary"),
            "context_findings_len": len(findings),
            "context_findings_head": findings[:2],
            "context_paths_len": len(paths),
            "context_paths_head": paths[:2],
            "context_paths_triple_total": total_triples,
            "graph_paths_strength": graph_paths_strength,
            "context_slot_limits": context_bundle.get("slot_limits"),
            "similar_seed_images": list(similar_seed_images),
            "similarity_edges_created": similarity_edges_created,
            "similarity_threshold": similarity_threshold,
            "similarity_candidates_considered": similarity_candidates_considered,
        })
        if graph_degraded:
            self._payload["graph_degraded"] = True
        if context_consistency is not None:
            self._payload["context_consistency"] = context_consistency
            if not context_consistency and context_consistency_reason:
                self._payload["context_consistency_reason"] = context_consistency_reason

    def record_consensus(self, consensus: Dict[str, Any]) -> None:
        if not self.enabled:
            return
        self._payload["consensus"] = dict(consensus)

    def record_evaluation(self, evaluation: Dict[str, Any]) -> None:
        if not self.enabled:
            return
        self._payload["evaluation"] = dict(evaluation)

    def record_fallback_history(self, history: List[Dict[str, Any]]) -> None:
        if not self.enabled:
            return
        self._payload["finding_fallback_history"] = [dict(entry) for entry in history]

    def payload(self) -> Dict[str, Any]:
        return dict(self._payload) if self.enabled else {}


__all__ = ["DebugPayloadBuilder"]
