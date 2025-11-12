# 🧩 PART I. “바로 고쳐야 하는 것”에 대한 상세 SPEC

---

## ✅ [Spec-01] 업서트 일관성 보장 (Upsert Consistency Enforcement)

### 🔹 목적

VLM 또는 폴백에서 생성된 finding이 정상적으로 그래프에 업서트되어야 함.
즉, `pre_upsert_findings_len > 0`일 경우 `upsert_receipt.finding_ids`가 반드시 비어 있지 않아야 함.

---

### 🔹 문제 진단

현재는 `pre_upsert_findings` 존재하나 `upsert_receipt.finding_ids = []`로 반환됨.
이는 다음 원인 중 하나로 추정:

* ID 스키마 불일치 (예: “FIND-12345” vs “FND-001”)
* `tx.commit()` 이전에 조회 수행
* Validation 단계에서 reject되었으나 warning만 출력
* Upsert 결과가 반환 객체에 제대로 propagate되지 않음

### 🔹 코드 체크 (2025-11-12 기준)

* `grounded-ai/api/routers/pipeline.py:516-553` — upsert 후 `graph_repo.upsert_case()`의 반환만 신뢰하고 즉시 재조회/검증을 수행하지 않음. `finding_ids`가 비어도 에러만 기록하고 degraded 상태로 계속 진행.
* `grounded-ai/api/services/dedup.py:8-33` — findings는 단순 `dedup_findings()` 만 거쳐 id/type/location/conf 유효성 검증이 없음. Spec에 언급된 Pydantic `FindingSchema` 미구현.
* `grounded-ai/api/services/graph_repo.py:428-471` — `_prepare_upsert_parameters()`가 image/report/finding 정규화를 담당하지만 `upsert_case()`에서는 호출되지 않아 타입/ID 미스매치가 그대로 MERGE에 전달됨.
* `docs/VisionPipelineDebug/ConfirmedProblemScope.md:92-124` — IMG_001 재현에서 `pre_upsert_findings_len > 0` yet `finding_ids=[]` 현상이 실제 발생함을 확인.

### 🔹 후속 조치 제안 (2025-11-13)

1. **Fallback 시 extra 필드 허용**  
   `finding_validation_failed (extra_forbidden: source)`가 시드 레코드를 막고 있으므로 `FindingSchema`를 `extra="ignore"`로 두거나, validation 전에 `source`, `model` 등의 메타 필드를 strip하는 얇은 어댑터가 필요하다.  
   - 책임 파일: `grounded-ai/api/services/finding_validation.py`, `grounded-ai/api/routers/pipeline.py`.
2. **Neo4j MERGE 파라미터 로깅 및 보정**  
   현재 `finding_upsert_mismatch`는 Neo4j가 여전히 빈 `finding_ids`를 반환하기 때문에 발생한다. `graph_payload["findings"]`, `_prepare_upsert_parameters()` 결과, 그리고 `UPSERT_CASE_QUERY` 실행 직후 `tx.run(...).single()`에서 얻은 값들을 구조적으로 로깅하여 어느 단계에서 ID가 사라지는지 추적해야 한다. 필요시 Cypher에 `CALL { ... RETURN fid, f }` 디버그 블록을 추가해 실제 `f.id`/`fd.id`를 비교한다.
3. **통합 테스트/스크립트 고도화**  
   `scripts/vision_pipeline_debug.sh`에서 사용하는 세 케이스를 pytest integration으로 옮겨 `force_dummy_fallback=true` 시 `finding_ids`가 비어 있지 않은지 검증한다. 실패 시 CI가 즉시 감지하도록 GitHub Actions 워크플로우에도 포함한다.

---

### 🔹 수정 목표

1. **Upsert 후 즉시 검증(requery) 절차 삽입**

   ```python
   result = repo.upsert_findings(image_id, findings)
   verified_ids = repo.verify_findings(image_id, [f.id for f in findings])
   assert set(verified_ids) == set(result.finding_ids)
   ```
2. **Validation Layer 명시화**

   ```python
   class FindingSchema(BaseModel):
       id: str
       type: str
       location: str
       conf: float
       size_cm: Optional[float]
   ```

   → type/location/conf/size_cm 중 하나라도 누락 시 hard error 발생.
3. **에러 정책 변경**

   * 기존: fail → degraded fallback
   * 변경: fail → explicit error `{"stage":"upsert","msg":"finding_upsert_mismatch"}`

---

### 🔹 검증 기준

| 항목                          | 기대값                              | 검증 방법                                                  |
| --------------------------- | -------------------------------- | ------------------------------------------------------ |
| pre_upsert_findings_len > 0 | upsert_receipt.finding_ids != [] | E2E test (pytest)                                      |
| finding_ids 재조회 일치율         | 100%                             | Neo4j 쿼리 `MATCH (i:Image)-[:HAS_FINDING]->(f:Finding)` |
| upsert_ms latency           | < 200ms                          | debug.log latency 필드 확인                                |

---



### 현재 시스템 메모

* `/grounded-ai/api/routers/pipeline.py`는 `graph_repo.upsert_case()` 직후 `FindingVerifier`로 재조회 및 비교를 수행하며 불일치 시 `_raise_upsert_mismatch`로 500을 던져 Spec 검증 흐름을 일부 만족함.
* `normalized_findings`의 `source` 필드는 `FindingSchema (extra="forbid")`가 차단하므로 `_validate_findings`에서 fallback `source`/`model` 키를 제거하거나 Schema를 `extra="ignore"`로 조정해 검증을 통과해야 함.
* `graph_repo._prepare_upsert_parameters()`와 `upsert_case()`는 현재 Neo4j 파라미터 및 `rec`만 반환하므로 Spec이 제안한 Cypher `fid` vs `fd` 로그와 파라미터를 `DebugPayload` 또는 별도 로깅으로 남겨 `finding_upsert_mismatch` 재현 가능성을 확보해야 함.
* `DebugPayloadBuilder.record_upsert()`는 `upsert_receipt`과 ID 리스트만 기록하므로 `graph_payload["findings"]`와 `_prepare_upsert_parameters()` 결과를 함께 기록해 `pre_upsert_findings_len > 0`인데 `finding_ids=[]`인 상황을 추적하는 것이 실용적.
* `scripts/vision_pipeline_debug.sh`에서 수행하던 `force_dummy_fallback=true` 테스트는 `tests/test_upsert_consistency.py` 같은 pytest 통합 테스트로 전환하여 CI에서 `pre_upsert_findings_len > 0` 조건에서도 `finding_ids`가 비어있지 않음을 자동으로 검증하도록 해야 함.

### Spec-01 액션 플랜

1. **검증 레이어 정비** – `_validate_findings`가 `FindingSchema`에 전달하기 전 `normalized_findings`에서 `source`/`model` 같은 보조 메타를 제거하거나 `FindingSchema`의 `extra` 설정을 조정하고, 검증 이후 디버깅용 메타를 다시 주입할 수 있는 구조를 마련 (`grounded-ai/api/routers/pipeline.py`, `grounded-ai/api/services/finding_validation.py`).
2. **Neo4j 업서트 로깅 확대** – `graph_repo._prepare_upsert_parameters()`와 `upsert_case()`의 입력/출력 (graph_payload, params, fid vs fd) 내용을 `DebugPayloadBuilder` 또는 별도 로깅에 남겨 `finding_upsert_mismatch` 발생 시 데이터를 재생산할 수 있도록 (`grounded-ai/api/services/graph_repo.py`, `grounded-ai/api/services/debug_payload.py`).
3. **명시적 실패 처리 정비** – `_raise_upsert_mismatch` 호출 시 `errors` 리스트에 `stage: upsert` 항목을 쌓고 즉시 500을 반환하여 degraded fallback이 아닌 실패로 흐르게 만들기 (`grounded-ai/api/routers/pipeline.py`).
4. **pytest 통합 흐름** – `scripts/vision_pipeline_debug.sh`의 `force_dummy_fallback` 케이스를 `tests/test_upsert_consistency.py`로 이전하고 CI 워크플로우에서 항상 실행하여 `pre_upsert_findings_len > 0`인 경우 `finding_ids`가 비어있지 않아야 함을 검증 (`tests/test_upsert_consistency.py`, `.github/workflows/ci.yml` 등).
5. **성과 지표 확보** – `timings["upsert_ms"]`나 debug 로그를 활용해 latency `<200ms` 기준을 유지하고, `upsert_success_rate`/`verify_match_rate` 지표를 수집해 문서(예: `docs/stabilization/spec.md` 메트릭 섹션)에도 반영 (`grounded-ai/api/routers/pipeline.py`, `docs/stabilization/spec.md` metrics).

## ✅ [Spec-02] 폴백 상태 덮어쓰기 방지 (Fallback State Integrity)

### 🔹 목적

`finding_fallback.force=true`로 실행 시, `finding_fallback.used=true`로 일관되게 유지되어야 함.

---

### 🔹 문제 진단

현재 `force_dummy_fallback=true` 옵션을 줘도 `used=false`로 남음.
→ 중간 단계(`graph_context_builder` or `analyzer.py`)에서 `finding_fallback` 객체를 다시 재할당하고 있음.

---

### 🔹 수정 목표

1. **단일 소스 구조체 도입**

   ```python
   class FallbackMeta(BaseModel):
       used: bool
       forced: bool
       strategy: Optional[str]
       registry_hit: bool
       seeded_ids: list[str]
   ```

   * `model_config = {"frozen": True}`
   * 모든 단계에서 deepcopy 금지, reference만 전달.

2. **읽기/쓰기 포인트 고정**

   * `normalize_image()` → 생성
   * `pipeline.analyze()` → 최종 참조
   * 중간 단계에서 수정 시 Exception 발생.

---

### 🔹 검증 기준

| 항목                          | 기대값                    | 검증 방법                         |
| --------------------------- | ---------------------- | ----------------------------- |
| `force_dummy_fallback` 실행 시 | used=true, forced=true | vision_pipeline_debug.sh 결과   |
| 중간 단계 재할당 시                 | ValidationError 발생     | pytest에서 try/except assertion |

---

## ✅ [Spec-03] 컨텍스트 단일화 (Context Source Unification)

### 🔹 목적

`context_paths`, `facts JSON`, `triples summary`가 모두 동일한 근거(Neo4j 쿼리 결과)에 기반해야 함.

---

### 🔹 문제 진단

동일 응답 내에 `"No path generated"`와 실제 path list가 공존함 → in-memory findings와 graph 쿼리 병용.

---

### 🔹 수정 목표

1. **컨텍스트 생성기 내부 구조 변경**

   ```python
   class ContextBuilder:
       def build(self, image_id: str):
           graph_result = self.repo.query_paths(image_id)
           # pre_upsert 결과는 UI 미리보기 용도로만 유지
           return {
               "paths": graph_result.paths,
               "facts": graph_result.facts,
               "triples_summary": graph_result.summary,
           }
   ```
2. **in-memory paths 사용 금지**

   * 단, fallback case에서 그래프에 데이터 없을 시 `"paths=[]"` 명시적 표시 (silent degrade 금지)

---

### 🔹 검증 기준

| 항목                                     | 기대값                 | 검증 방법           |
| -------------------------------------- | ------------------- | --------------- |
| facts JSON vs context_findings_head    | 동일                  | `jq` diff 결과 동일 |
| triples_summary 내부 `No path generated` | 존재 시 paths_len == 0 | 일관성 체크 pytest   |

---

## ✅ [Spec-04] 슬롯 리밸런싱 개선 (Slot Rebalancing Fix)

### 🔹 목적

findings 슬롯이 첫 miss 이후 0으로 고정되지 않고, 최소 한 번 이상 재탐색하도록 보장.

---

### 🔹 문제 진단

이전 버그: 첫 쿼리에서 ctx_paths_len=0 → `k_findings=0`으로 재할당 → 이후 루프 전체에서 재시도 안 함.

---

### 🔹 수정 목표

1. **하한 보장**

   ```python
   k_findings = max(requested_k, 1)
   ```
2. **리밸런스 루프 변경**

   ```python
   for slot in slots:
       if slot == "findings" and results[slot].empty:
           results[slot] = self.repo.retry_paths(image_id, slot)
   ```
3. **slot_meta에 retry_flag 추가**

   ```json
   "slot_meta": {
     "requested_k": 2,
     "applied_k": 2,
     "retried_findings": true
   }
   ```

---

### 🔹 검증 기준

| 항목                          | 기대값               | 검증 방법            |
| --------------------------- | ----------------- | ---------------- |
| CT 케이스에서도 findings slot 재탐색 | ctx_paths_len ≥ 1 | E2E debug output |
| slot_meta.retried_findings  | true              | 로그 필드 검증         |

---

## ✅ [Spec-05] 라벨·위치 표준화 (Ontology Label Standardization)

### 🔹 목적

동일 입력 이미지 재실행 시 라벨·위치 불안정성(Subarachnoid Hemorrhage ↔ Hypodensity 등)을 제거.

---

### 🔹 구현 목표

1. **Ontology 사전 정의**

   ```python
   LABEL_CANONICAL = {
       "Subarachnoid Hemorrhage": ["SAH", "Subarachnoid Bleeding"],
       "Hypodensity": ["Low attenuation area", "Reduced density"]
   }
   ```

2. **타이브레이커 규칙**

   * 동일 confidence 시, 사전 정의 우선순위 사용
   * 라벨 매칭은 대소문자/공백/특수문자 무시 비교

3. **위치 매핑**

   ```python
   LOCATION_MAP = {
       "Cerebral Hemispheres": "Left parietal lobe",
       "Right hepatic lobe": "Right lobe of the liver"
   }
   ```

---

### 🔹 검증 기준

| 항목                         | 기대값              | 검증 방법      |
| -------------------------- | ---------------- | ---------- |
| 동일 이미지 재실행 시 라벨 변동률        | < 5%             | N=10 반복 실행 |
| 라벨 매칭 규칙                   | canonical key 유지 | diff 비교    |
| confidence 기반 tie-breaking | 재현성 확보           | 스냅샷 비교     |

---

# 🧪 PART II. 논문을 위한 “최소 실험 계획” Spec

---

## 🎯 목적

본 논문의 목표는 “Graph-grounded Vision-Language 파이프라인의 구조적 안정화 및 초기 성능 검증”임.
따라서 정량 실험은 **신뢰도(Upsert/Consistency), 경로 커버리지, 합의 안정성, 언어 품질**에 초점을 둬야 함.

---

## 🧭 [Exp-01] 업서트 신뢰성 (Upsert Reliability Evaluation)

### 🔹 설계

* **데이터셋**: dummy_seed 10건 + real VLM 10건 (CT/US 혼합)
* **메트릭**

  | Metric                | 정의                                   |
  | --------------------- | ------------------------------------ |
  | `upsert_success_rate` | (finding_ids 생성 케이스 / 전체)            |
  | `verify_match_rate`   | Neo4j 쿼리 결과와 receipt.finding_ids 일치율 |
  | `upsert_latency_ms`   | 평균 업서트 시간                            |

### 🔹 실행

```bash
pytest tests/test_upsert_consistency.py -k "test_upsert_integrity"
```

### 🔹 수용 기준

* `upsert_success_rate ≥ 0.95`
* `verify_match_rate = 1.0`
* 평균 latency ≤ 200ms

---

## 🧭 [Exp-02] 경로 커버리지 (Context Path Coverage)

### 🔹 설계

* **목적**: 업서트 성공 이후 그래프의 정보 풍부도 측정.
* **메트릭**

  | Metric               | 정의                           |
  | -------------------- | ---------------------------- |
  | `ctx_paths_len`      | 생성된 path 수                   |
  | `triples_total`      | 전체 triple 수                  |
  | `relation_type_dist` | HAS_FINDING, LOCATED_IN 등 비율 |

### 🔹 실행

```bash
pytest tests/test_context_paths.py
```

### 🔹 수용 기준

* 평균 `ctx_paths_len ≥ 2`
* 관계 다양도(`relation_type_dist["HAS_FINDING"] / total ≥ 0.4`)
* `graph_paths_strength ≥ 0.3`

---

## 🧭 [Exp-03] 합의 안정성 (Consensus Stability)

### 🔹 설계

* **목적**: multi-mode 합의의 일관성 검증
* **케이스**: 동일 이미지 5회 재실행
* **메트릭**

  | Metric                                    | 정의   |
  | ----------------------------------------- | ---- |
  | `agreement_score_std`                     | 표준편차 |
  | `supporting_modes` / `disagreed_modes` 빈도 |      |
  | `confidence_mode` 일관성                     |      |

### 🔹 실행

```bash
pytest tests/test_consensus_stability.py
```

### 🔹 수용 기준

* `agreement_score_std ≤ 0.05`
* `confidence_mode` 재현율 ≥ 0.9

---

## 🧭 [Exp-04] 언어 품질 검증 (Clinical Text Sanity)

### 🔹 설계

* **목적**: VGL 출력에서 비전문 용어 및 내부 술어 노출 방지
* **방법**: 금지어 사전 기반 자동 평가

  ```python
  FORBIDDEN = ["뾰루지", "LOCATED_IN", "RELATED_TO"]
  ```
* **메트릭**

  * `forbidden_hit_rate` = (금지어 등장 문장 수 / 전체 문장 수)

### 🔹 수용 기준

* `forbidden_hit_rate = 0`
* 평균 문장 길이 30±10 token

---

## 🧭 [Exp-05] 리밸런싱 효과 검증 (Slot Rebalancing Ablation)

### 🔹 설계

* **목적**: 기존 vs 개선된 리밸런스 로직 비교
* **환경**

  * 기존: k_findings=0 허용
  * 개선: k_findings ≥ 1 유지
* **메트릭**

  | Metric               | 정의                |
  | -------------------- | ----------------- |
  | `ctx_paths_len_diff` | 두 버전 간 평균 경로 수 차이 |
  | `degraded_rate`      | degraded=true 비율  |

### 🔹 수용 기준

* `ctx_paths_len_diff ≥ +1`
* `degraded_rate` 개선 ≥ 30%

---

# 🧱 종합 Validation Matrix

| 실험     | 주요 타깃                 | 통과 기준        | 기대 개선율 |
| ------ | --------------------- | ------------ | ------ |
| Exp-01 | Upsert Reliability    | success ≥95% | +80%   |
| Exp-02 | Path Coverage         | avg_paths ≥2 | +40%   |
| Exp-03 | Consensus Stability   | std ≤0.05    | +60%   |
| Exp-04 | Clinical Text Quality | forbidden=0  | +100%  |
| Exp-05 | Slot Rebalance        | degraded↓30% | +30%   |
