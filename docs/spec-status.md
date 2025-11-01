# Spec Status Tracker

| Requirement | Description                         | Current Status | Notes |
|-------------|-------------------------------------|----------------|-------|
| R1          | Graph context coverage              | In Progress    | TOPK_PATHS_QUERY 다중 패턴 + ContextPack dedup (graph_repo.py, context_pack.py) |
| R2          | Image upsert idempotency            | In Progress    | storage_uri 우선 MERGE 적용 (graph_repo.py#UPSERT_CASE_QUERY) |
| R3          | Consensus & language hygiene        | Pending        |  |
| R4          | Evidence summary completeness       | In Progress    | SUMMARY 관계 확장 및 slot_limits 반환 (graph_repo.py, context_pack.py) |
| R5          | Similarity exploration controls     | Pending        |  |
| R6          | Tooling & CI enablement             | Pending        |  |

> pytest grounded-ai/tests/unit/test_context_pack.py ✅; 전체 pytest 는 neo4j/redis 모듈 부재로 중단됨.
