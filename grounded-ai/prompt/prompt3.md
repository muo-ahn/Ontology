api/services/context_pack.py 를 작성하세요. 기능:

- class GraphContextBuilder(GraphRepo 상속 또는 합성)
  - build_prompt_context(image_id: str, k: int=2, mode: str="triples"|"json") -> str
    1) bundle = query_bundle(image_id)  // summary + facts
    2) paths = query_paths(image_id, k)
    3) 문자열로 포맷:
       [EDGE SUMMARY]
       REL: cnt=?, avg_conf=?
       ...
       [EVIDENCE PATHS (Top-k)]
       1) label
          triple1
          triple2
       ...
       [FACTS JSON]
       {...}
    - mode="json"인 경우, bundle["facts"]만 JSON 문자열로 반환.

- json_dumps_safe(obj) 유틸 포함 (ensure_ascii=False)
