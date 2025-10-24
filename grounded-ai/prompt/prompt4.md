api/routers/graph.py 를 작성하고, /graph/upsert, /graph/context 구현:

- POST /graph/upsert
  Body: Prompt 2의 upsert_case payload
  Action: GraphRepo.upsert_case 호출 → {"ok":true}

- GET /graph/context
  Query: image_id, k=2, mode=triples|json
  Action: GraphContextBuilder.build_prompt_context 호출 → {"context": "..."}
