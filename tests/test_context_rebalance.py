from __future__ import annotations

import sys
import types

if "py2neo" not in sys.modules:
    py2neo_stub = types.ModuleType("py2neo")
    py2neo_stub.Graph = object  # type: ignore[attr-defined]
    py2neo_stub.Node = object  # type: ignore[attr-defined]
    py2neo_stub.Relationship = object  # type: ignore[attr-defined]
    sys.modules["py2neo"] = py2neo_stub
    errors_stub = types.ModuleType("py2neo.errors")
    errors_stub.ClientError = Exception  # type: ignore[attr-defined]
    sys.modules["py2neo.errors"] = errors_stub

if "neo4j" not in sys.modules:
    neo4j_stub = types.ModuleType("neo4j")
    sys.modules["neo4j"] = neo4j_stub
    neo4j_stub.GraphDatabase = object  # type: ignore[attr-defined]
    exceptions_stub = types.ModuleType("neo4j.exceptions")
    exceptions_stub.Neo4jError = Exception  # type: ignore[attr-defined]
    sys.modules["neo4j.exceptions"] = exceptions_stub

from services.context_pack import _rebalance_slot_limits


def _path(slot: str) -> dict:
    return {"label": slot, "triples": [f"Image -{slot.upper()}-> Node"], "slot": slot}


def test_rebalance_when_findings_empty_gives_report_slot() -> None:
    slots = {"findings": 2, "reports": 0, "similarity": 0}
    rebalanced = _rebalance_slot_limits(slots, [])
    assert sum(rebalanced.values()) == 2
    assert rebalanced["reports"] >= 1
    assert rebalanced["findings"] in {0, 1}


def test_rebalance_preserves_allocations_with_results() -> None:
    slots = {"findings": 2, "reports": 1, "similarity": 0}
    paths = [_path("findings")]
    rebalanced = _rebalance_slot_limits(slots, paths)
    assert rebalanced["findings"] >= 1
    assert sum(rebalanced.values()) == sum(slots.values())


def test_rebalance_prefers_slots_with_returned_paths() -> None:
    slots = {"findings": 2, "reports": 0, "similarity": 0}
    paths = [_path("reports")]
    rebalanced = _rebalance_slot_limits(slots, paths)
    assert rebalanced["reports"] >= 1
    assert sum(rebalanced.values()) == 2
