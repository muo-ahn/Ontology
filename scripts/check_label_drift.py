#!/usr/bin/env python3
"""Detect non-canonical labels/locations in dummy datasets."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Dict, List

from services.dummy_registry import DummyFindingRegistry
from services.ontology_map import canonicalise_label, canonicalise_location


def main() -> None:
    mismatches: List[Dict[str, str]] = []
    for image_id in DummyFindingRegistry.available_image_ids():
        for stub in DummyFindingRegistry.resolve(image_id):
            if not stub.type and not stub.location:
                continue
            canonical_label, label_rule = canonicalise_label(stub.type)
            canonical_location, location_rule = canonicalise_location(stub.location)
            if (label_rule is None or label_rule == "unchanged") and stub.type:
                mismatches.append(
                    {
                        "image_id": image_id,
                        "field": "type",
                        "raw": stub.type,
                        "canonical": canonical_label or "",
                        "rule": label_rule or "",
                    }
                )
            if (location_rule is None or location_rule == "unchanged") and stub.location:
                mismatches.append(
                    {
                        "image_id": image_id,
                        "field": "location",
                        "raw": stub.location,
                        "canonical": canonical_location or "",
                        "rule": location_rule or "",
                    }
                )

    artifacts_dir = Path("artifacts")
    artifacts_dir.mkdir(exist_ok=True)
    report_path = artifacts_dir / "label_drift.json"
    report_path.write_text(json.dumps({"mismatches": mismatches}, ensure_ascii=False, indent=2), encoding="utf-8")

    if mismatches:
        print(json.dumps({"mismatches": mismatches}, ensure_ascii=False, indent=2))
        sys.exit(1)
    print("No label drift detected.")


if __name__ == "__main__":
    main()
