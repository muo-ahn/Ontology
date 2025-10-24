api/services/context_pack.py 를 작성하세요. 기능:

- class GraphContextBuilder(GraphRepo 상속 또는 합성)
  - build_prompt_context(image_id: str, k: int=2, mode: str="triples"|"json") -> str
    1) edge_summary = query_edge_summary(image_id)
    2) hits = query_topk_paths(image_id, k)
    3) facts = query_facts(image_id)
    4) 문자열로 포맷:
       [EDGE SUMMARY]
       REL: cnt=?, avg_conf=?
       ...
       [EVIDENCE PATHS (Top-k)]
       1) (Image IMG)-[HAS_FINDING]->(Finding type=..., size=..., conf=...)
          (Finding ...)-[LOCATED_IN]->(Anatomy ...)
          (Image IMG)-[DESCRIBED_BY]->(Report id=..., conf=...)
       ...
       [FACTS JSON]
       {...}
    - mode="json"인 경우, facts만 JSON 문자열로 반환.

- json_dumps_safe(obj) 유틸 포함 (ensure_ascii=False)
