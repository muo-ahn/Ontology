api/services/graph_repo.py 를 작성하세요. 기능:

- class GraphRepo(uri, user, pwd)
  - upsert_case(payload: dict) -> None
    입력 예시:
    {
      "case_id":"C_001",
      "image":{"image_id":"IMG_001","path":"/data/img_001.png","modality":"XR"},
      "report":{"id":"r_1","text":"Chest X-ray – probable RUL nodule (~1.8 cm).","model":"qwen2-vl","conf":0.83,"ts":"2025-10-23T12:00:00"},
      "findings":[{"id":"f_1","type":"nodule","location":"RUL","size_cm":1.8,"conf":0.87}]
    }
    Cypher(엣지 필수):
      MERGE (c:Case {id:$case_id})
      MERGE (i:Image {image_id:$image.image_id})
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

  - query_bundle(image_id: str) -> dict
    MATCH (i:Image {image_id:$image_id})
    OPTIONAL MATCH (i)-[:HAS_FINDING]->(f:Finding)
    OPTIONAL MATCH (f)-[:LOCATED_IN]->(a:Anatomy)
    WITH i,
         count(f) AS cnt_f,
         round(coalesce(avg(f.conf), 0.0), 2) AS avg_f,
         collect(DISTINCT CASE
             WHEN f IS NULL THEN NULL
             ELSE {
               type: toLower(coalesce(f.type, '')),
               location: coalesce(a.name, f.location, ''),
               size_cm: toFloat(coalesce(f.size_cm, 0)),
               conf: round(coalesce(f.conf, 0), 2)
             }
         END) AS findings_raw
    WITH i,
         cnt_f,
         avg_f,
         [f IN findings_raw WHERE f IS NOT NULL] AS findings
    OPTIONAL MATCH (i)-[:DESCRIBED_BY]->(r:Report)
    WITH i,
         cnt_f,
         avg_f,
         findings,
         count(r) AS cnt_r,
         round(coalesce(avg(r.conf), 0.0), 2) AS avg_r
    WITH {
      image_id: i.image_id,
      summary: [s IN [
          {rel:'HAS_FINDING', cnt: cnt_f, avg_conf: round(coalesce(avg_f,0),2)},
          {rel:'DESCRIBED_BY', cnt: cnt_r, avg_conf: round(coalesce(avg_r,0),2)}
      ] WHERE s.cnt <> 0],
      facts: {image_id: i.image_id, findings: findings}
    } AS bundle
    RETURN bundle

  - query_paths(image_id: str, k: int=2) -> List[dict]
    // Score = $alpha_finding * finding_conf + $beta_report * report_conf
    MATCH (i:Image {image_id:$image_id})-[:HAS_FINDING]->(f:Finding)
    OPTIONAL MATCH (f)-[:LOCATED_IN]->(a:Anatomy)
    OPTIONAL MATCH (i)-[:DESCRIBED_BY]->(rep:Report)
    WITH i, f, a, rep,
         toLower(coalesce(f.type, 'finding')) AS f_type,
         coalesce(f.id, '?') AS f_id,
         coalesce(a.name, f.location, '') AS location,
         toFloat(coalesce(f.size_cm, 0)) AS size_cm,
         coalesce(f.conf, 0) AS finding_conf,
         coalesce(rep.conf, 0) AS report_conf,
         coalesce(rep.id, '?') AS rep_id,
         coalesce(rep.model, '') AS rep_model,
         coalesce(toString(rep.ts), '') AS rep_ts
    WITH i, f_type, f_id, location, size_cm, finding_conf, report_conf, rep, rep_id, rep_model, rep_ts,
         finding_conf * $alpha_finding + report_conf * $beta_report AS score
    WITH i,
         CASE WHEN f_id <> '?' THEN f_type + '#' + f_id ELSE f_type END AS finding_label,
         location, size_cm, finding_conf, report_conf, rep, rep_id, rep_model, rep_ts, score,
         [t IN [
             '(Image ' + i.image_id + ')-[HAS_FINDING]->(' + finding_label + ' | location=' + location + ', size_cm=' + toString(round(size_cm, 2)) + ', conf=' + toString(round(finding_conf, 2)) + ')',
             CASE WHEN location = '' THEN NULL ELSE '(' + finding_label + ')-[LOCATED_IN]->(' + location + ')' END,
             CASE WHEN rep IS NULL THEN NULL ELSE '(Image ' + i.image_id + ')-[DESCRIBED_BY]->(Report#' + rep_id + ' | model=' + rep_model + ', conf=' + toString(round(report_conf, 2)) + ', ts=' + rep_ts + ')' END
         ] WHERE t IS NOT NULL] AS triples
    WITH i,
         CASE WHEN score IS NULL THEN finding_label ELSE finding_label + ' [score=' + toString(round(score, 2)) + ']' END AS label,
         triples,
         score
    ORDER BY score DESC, label
    WITH i,
         reduce(acc = [], path IN collect({label: label, triples: triples, score: score}) |
             CASE WHEN any(existing IN acc WHERE existing.label = path.label AND existing.triples = path.triples)
                  THEN acc
                  ELSE acc + [path]
             END
         ) AS deduped
    WITH [p IN deduped | {label: p.label, triples: p.triples}][0..$k] AS sliced
    RETURN [p IN sliced | {label: p.label, triples: p.triples}] AS paths
