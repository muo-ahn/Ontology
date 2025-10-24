api/services/graph_repo.py 를 작성하세요. 기능:

- class GraphRepo(uri, user, pwd)
  - upsert_case(payload: dict) -> None
    입력 예시:
    {
      "case_id":"C_001",
      "image":{"id":"IMG_001","path":"/data/img_001.png","modality":"XR"},
      "report":{"id":"r_1","text":"Chest X-ray – probable RUL nodule (~1.8 cm).","model":"qwen2-vl","conf":0.83,"ts":"2025-10-23T12:00:00"},
      "findings":[{"id":"f_1","type":"nodule","location":"RUL","size_cm":1.8,"conf":0.87}]
    }
    Cypher(엣지 필수):
      MERGE (c:Case {id:$case_id})
      MERGE (i:Image {id:$image.id})
      ON CREATE SET i.path=$image.path, i.modality=$image.modality
      MERGE (c)-[:HAS_IMAGE]->(i)
      MERGE (r:Report {id:$report.id})
      SET r.text=$report.text, r.model=$report.model, r.conf=$report.conf, r.ts=datetime($report.ts)
      MERGE (i)-[:DESCRIBED_BY]->(r)
      FOREACH (f IN $findings |
        MERGE (fd:Finding {id:f.id})
        SET fd.type=f.type, fd.location=f.location, fd.size_cm=f.size_cm, fd.conf=f.conf
        MERGE (i)-[:HAS_FINDING]->(fd)
      )

  - query_edge_summary(image_id: str) -> List[dict]
    MATCH (i:Image {id:$image_id})-[rel]->(x)
    WITH type(rel) AS reltype, count(*) AS cnt, round(avg(coalesce(rel.conf,0.5))*100)/100 AS avg_conf
    RETURN reltype, cnt, avg_conf
    ORDER BY cnt DESC, avg_conf DESC

  - query_topk_paths(image_id: str, k: int=2) -> List[dict]
    근거 경로 Top-k (score 기반):
    MATCH (i:Image {id:$image_id})-[:HAS_FINDING]->(f:Finding)
    OPTIONAL MATCH (f)-[r1:LOCATED_IN]->(a:Anatomy)
    OPTIONAL MATCH (i)-[r2:DESCRIBED_BY]->(rep:Report)
    WITH i,f,a, r1,rep, r2,
         coalesce(f.conf,0.5) AS f_conf,
         coalesce(r1.conf,0.5) AS loc_conf,
         coalesce(r2.conf,0.5) AS rep_conf,
         coalesce(f.ts, datetime("1970-01-01")) AS f_ts
    WITH i,f,a,rep,(0.6*f_conf+0.3*loc_conf+0.1*rep_conf) AS score, f_ts
    ORDER BY score DESC, f_ts DESC
    WITH i, collect({f:f{.*}, a:a{.*}, rep:rep{.*}, score:score})[0..$k] AS hits
    RETURN hits

  - query_facts(image_id: str) -> dict
    MATCH (i:Image {id:$image_id})-[:HAS_FINDING]->(f:Finding)
    OPTIONAL MATCH (f)-[:LOCATED_IN]->(a:Anatomy)
    RETURN i.id AS image_id,
           collect({type:f.type, location:a.name, size_cm:f.size_cm, conf:f.conf}) AS findings
