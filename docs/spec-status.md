# Spec Status Tracker

| Requirement | Description                         | Current Status | Notes |
|-------------|-------------------------------------|----------------|-------|
| R1          | Graph context coverage              | Completed      | Slot rebalance + multi-pattern paths (graph_repo.py, context_pack.py) |
| R2          | Image upsert idempotency            | Completed      | storage_uri-first reuse with repeated upsert test (graph_repo.py, tests/test_paths_and_analyze.py) |
| R3          | Consensus & language hygiene        | Pending        |  |
| R4          | Evidence summary completeness       | In Progress    | SUMMARY relation expansion & slot_limits output (graph_repo.py, context_pack.py) |
| R5          | Similarity exploration controls     | Pending        |  |
| R6          | Tooling & CI enablement             | Pending        |  |

> pytest grounded-ai/tests/unit/test_context_pack.py ✅; full pytest still requires local Neo4j/Redis modules.
