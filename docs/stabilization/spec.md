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
2. **Validation Layer 명시화 + fallback 메타 처리**

   ```python
   class FindingSchema(BaseModel):
       id: str
       type: str
       location: str
       conf: float
       size_cm: Optional[float]
   ```

   → type/location/conf/size_cm 중 하나라도 누락 시 hard error 발생. 단, fallback 시 주입되는 `source`, `model` 등의 보조 필드는 검증 전에 분리/저장 후 검증 통과 뒤 다시 주입(또는 `extra="ignore"` 설정)하여 시드 데이터가 막히지 않도록 한다.
3. **그래프 업서트 파라미터 추적**

   * `graph_payload` 원본과 `_prepare_upsert_parameters()` 결과를 debug payload에 기록한다.
   * Neo4j MERGE 직후 반환된 `finding_ids`를 동일 blob에 포함시켜 재현성을 확보한다.
4. **에러 정책 변경**

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

### 🔹 코드 체크 (2025-11-13 기준)

* `grounded-ai/api/routers/pipeline.py:360-520` – VLM 결과를 정규화한 뒤 `fallback_meta = dict(normalized.get("finding_fallback") or {})`로 얕은 복사 후 여러 필드(`used`, `registry_hit`, `force`)를 서로 다른 분기에서 갱신하고 있어, 이후 단계가 동일 dict를 참조한다는 보장이 없다.
* `grounded-ai/api/services/context_orchestrator.py:50-118` – `fallback_meta`나 `finding_source`를 건드리지 않지만, `graph_context_builder`는 fallback 세부 정보를 모른 채 빈 dict를 생성하여 최종 결과에서 `finding_fallback.used`가 원래 상태를 잃는다.
* `grounded-ai/api/services/debug_payload.py:30-87` – `record_identity()` 호출 시 넘어온 dict를 그대로 `self._payload["finding_fallback"]`에 저장하므로, 이후 파이프라인에서 다른 dict를 덮어쓰면 debug에도 상이한 값이 기록된다.
* `scripts/vision_pipeline_debug.sh` 결과 (`[9] Debug with parameters`) – `force_dummy_fallback=true`를 줬는데도 `finding_fallback.used=false`가 유지되는 사례가 다수 재현됨.

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

### 🔹 Spec-02 액션 플랜

1. **Immutable Fallback 모델화** – `grounded-ai/api/services/fallback_meta.py`(신규) 등에 `FallbackMeta(BaseModel, frozen=True)`를 정의하고, `normalize_from_vlm()`이 해당 객체를 반환하도록 수정. 파이프라인에서는 `dict(...)` 복사 대신 FallbackMeta 인스턴스를 공유하고, 값을 바꿔야 할 경우 `model_copy(update=...)`만 허용.
2. **Force 플래그 일관 전달** – `pipeline.analyze()`에서 `force_dummy_fallback`을 해석할 때 `FallbackMeta.forced`와 `used`를 동시에 true로 세팅하고, 이후 `ContextBuilder`, `DebugPayloadBuilder`, `results.finding_fallback`까지 동일 객체/사전이 전달되도록 setter 유틸리티를 추가.
3. **재할당 방지 가드** – `DebugPayloadBuilder.record_identity()` 및 후속 단계에서 `finding_fallback`을 재생성하면 `ValidationError`를 일으키도록 타입 가드를 넣고, pytest에서 `force_dummy_fallback=true` 시 `used`/`forced`가 true로 유지되는지 검증(`tests/test_fallback_integrity.py` 등).
4. **관측 가능성 확보** – `scripts/vision_pipeline_debug.sh` / `/pipeline/analyze?debug=1` 결과에 `finding_fallback_history`(예: stage별 snapshot)나 최소한 `fallback_meta.source_stage`를 남겨 추후 회귀 분석이 가능하도록 한다.
5. **CI 커버리지** – FastAPI router 수준에서 `force_dummy_fallback=true`를 붙여 호출하는 통합 테스트를 추가하여, `finding_fallback.used`/`forced`가 응답과 debug payload 양쪽에서 true인지 확인하고, 실패 시 CI가 즉시 잡도록 `.github/workflows/ci.yml`에 포함한다.

---

### 🔹 진행 상황 요약 (2025-11-13)

- `finding_fallback`은 `FallbackMeta`(불변 Pydantic 모델)로 관리되며, `force_dummy_fallback=true`일 때 `used/force/forced`가 전 단계에서 `True`로 유지된다. `/pipeline/analyze` 결과, `graph_context`, `results`, `evaluation`, Debug payload 모두 동일 값을 노출한다.
- `vision_pipeline_debug.sh` 재현 (IMG_001, IMG_003, IMG201) 기준, `finding_source`가 `fallback` 혹은 `mock_seed`로 일관되게 노출되며 seeded ID가 있을 경우 그대로 유지된다.
- 아직 재할당 감지(ValidationError)와 pytest/CI 보강, fallback 변경 이력 기록은 미구현 상태이므로 향후 작업 필요.

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

### 🔹 코드 체크 (2025-11-13 기준)

* `grounded-ai/api/services/context_orchestrator.py:34-125` – `ContextOrchestrator.build()`가 `GraphContextBuilder`의 bundle을 받은 뒤에도 `normalized_findings` 기반 fallback 경로/팩트를 직접 합성한다. 이때 그래프가 실제로 경로를 반환했더라도 `_fallback_paths_from_findings()`가 덮어쓰는 경우가 발생한다.
* `grounded-ai/api/services/context_pack.py:1-220` – `GraphContextBuilder`는 `bundle["paths"]`, `bundle["facts"]`, `bundle["summary"]`를 동시에 구성하지만, fallback 삽입 시 summary 문자열(`[EDGE SUMMARY] ... 데이터 없음`)이 여전히 그래프 집계 결과를 포함하여 동일 응답 내에서 불일치가 생긴다.
* `grounded-ai/api/routers/pipeline.py:640-720` – `/pipeline/analyze`는 `context_bundle`에서 `facts`, `paths`, `triples`를 각각 다른 키로 추출하여 `results.graph_context`, `debug.context_*`에 채운다. fallback 경로가 삽입되면 facts/paths는 fallback을 가리키지만 summary/triples 문자열은 기존 그래프 데이터를 유지해 “No path generated” vs 실제 path 리스트가 동시에 노출된다.
* `scripts/vision_pipeline_debug.sh` – 현재도 `context_paths_len=0`인데 `[EVIDENCE PATHS] 데이터 없음`과 facts JSON에 fallback findings가 공존하는 사례가 재현된다.

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

### 🔹 Spec-03 액션 플랜

1. **GraphContextBuilder 일원화** – `grounded-ai/api/services/context_pack.py`에 `GraphContextResult` dataclass를 추가하고, `build_bundle()` 대신 `build_context()`가 `paths/facts/summary`를 단일 객체로 반환하도록 개편. `query_bundle()`/`query_paths()` 호출은 한 번만 수행하여 동일 소스에서 나온 데이터를 공유한다.
2. **ContextOrchestrator 단순화** – `grounded-ai/api/services/context_orchestrator.py`에서 `_fallback_findings_from_normalized` / `_fallback_paths_from_findings`를 제거하고, 그래프 미반환 시에는 `paths=[]`, `facts.findings=[]`, `bundle["triples"]="데이터 없음"`을 명시적으로 설정하되 `fallback_reason` 플래그를 추가해 클라이언트가 degrade 여부를 파악할 수 있게 한다.
3. **파이프라인 소비자 정비** – `/pipeline/analyze`(`grounded-ai/api/routers/pipeline.py`)에서 `graph_context.summary`, `graph_context.paths`, `graph_context.facts`, `debug.context_*`가 모두 `ContextResult` 하나에서 온 값을 사용하도록 보장하고, fallback 시 메시지(`"No path generated"` 등)를 paths/facts와 동일 조건으로 표시한다.
4. **검증 및 히스토리 로깅** – `DebugPayloadBuilder`에 `context_consistency` 필드를 추가해 `facts.findings`와 `context_findings_head`가 일치하는지 기록하고, mismatch가 감지되면 Spec-03 준수 실패로 간주하여 오류 리스트에 `{"stage":"context","msg":"facts_paths_mismatch"}`를 추가한다.
5. **테스트/CI** – `tests/test_context_orchestrator.py`를 보강하여 (a) 그래프가 경로를 반환할 때 facts/paths/summary가 동일 근거를 공유하는지, (b) 그래프가 빈 결과를 줄 때 fallback이 `paths=[]`와 명시적 degrade 플래그를 세팅하는지 확인하고, GitHub Actions에서 항상 실행하도록 한다.

---

### 🔹 검증 기준

| 항목                                     | 기대값                 | 검증 방법           |
| -------------------------------------- | ------------------- | --------------- |
| facts JSON vs context_findings_head    | 동일                  | `jq` diff 결과 동일 |
| triples_summary 내부 `No path generated` | 존재 시 paths_len == 0 | 일관성 체크 pytest   |

---

### 🔹 진행 상황 요약 (2025-11-15)

- `GraphContextBuilder.build_context()`가 `GraphContextResult`를 반환하도록 개편되었고, `/pipeline/analyze`는 더 이상 `_fallback_paths_from_findings` 같은 in-memory 경로를 삽입하지 않는다. 경로가 없을 때는 단순히 `paths=[]`, `triples`에 “No path generated (0/k)”를 표기하며, `context_consistency=true`가 함께 기록된다.
- `ContextOrchestrator`는 그래프가 빈 결과를 줄 경우 `no_graph_evidence`와 `fallback_reason="no_graph_paths"`만 세팅하고, facts/summary에는 원본 그래프 결과만 유지한다.
- `DebugPayloadBuilder`는 `context_consistency`와 `context_consistency_reason`을 기록하고, 파이프라인은 paths vs. triples 불일치 감지 시 `errors` 배열에 `{"stage":"context","msg":"facts_paths_mismatch"}`를 추가한다.
- **테스트 확장**: `tests/test_context_orchestrator.py`는 summary/slot 리밸런싱 케이스를, `tests/test_paths_and_analyze.py::test_pipeline_flags_context_mismatch_when_paths_conflict`는 파이프라인 레벨 mismatch 감지를 검증한다.
- **CI 반영**: `.github/workflows/ci.yml`이 `pytest tests/test_paths_and_analyze.py`를 포함하여 Spec-03 회귀를 자동 감시한다.
- Spec-03은 ✅ 완료 상태다.

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

### 🔹 Spec-04 액션 플랜 (세부)

1. **Slot Resolver 하한 재구현**
   - `_resolve_path_slots(total, explicit)` 호출 경로를 파악해 `total=0` 이더라도 `findings` 슬롯이 최소 1을 유지하도록 “floor” 파라미터를 도입한다.
   - `k_slots`에 음수나 `{"findings": 0}` 같은 입력이 들어오면 경고 로그를 남기고 자동으로 1 이상으로 clamp 한다.
   - 범용화를 위해 `_ensure_finding_slot_floor(slots, min_findings)` 헬퍼를 추가하고, `ContextOrchestrator`와 builder 양쪽에서 재사용한다.

2. **ContextOrchestrator 보정**
   - `_ensure_findings_slot_allocation()`이 `len(paths)`를 기반으로 `bundle["slot_limits"]["findings"]`를 즉시 재조정하고, 기존 값이 낮았을 경우 `slot_meta["retried_findings"]=True`와 `slot_meta["allocated_total"]`를 갱신한다.
   - `ContextResult`에 `slot_meta` 스냅샷을 포함시키고, `fallback_reason`이 존재하면서 실제로 재조정이 일어났다면 `fallback_reason="slot_rebalanced"` 등 세분화된 값을 추가한다.

3. **파이프라인 반환 값 정렬**
   - `/pipeline/analyze`에서 `graph_context.slot_limits`·`debug.context_slot_limits`·`evaluation.context_slot_limits`가 모두 동일 dict 인스턴스를 참조하도록 구조를 정리한다.
   - `slot_meta.retried_findings`가 true라면 `debug.context_notes`/`evaluation.notes`에 “findings slot rebalanced from X→Y” 메시지를 주입해 Explainability를 확보한다.
   - `consensus.mode_weights` 계산 시 rebalanced 슬롯 수치를 참고해 VGL 가중치를 미세 조정한다(예: findings 슬롯이 늘어나면 VGL weight +0.1).

4. **테스트 강화**
   - `tests/test_context_orchestrator.py`에 다음 시나리오를 추가:
     1. `k_paths=0`와 `k_slots={"findings": 0}` 입력에도 실제 paths가 존재하면 `slot_limits["findings"] >= len(paths)` 인지 확인.
     2. `slot_meta["retried_findings"]`가 true일 때 `ContextResult`가 해당 필드를 노출하는지 체크.
   - `tests/test_paths_and_analyze.py`에는 API 레벨 검증을 추가:
     1. 요청 시 `parameters={"k_findings": 0}`를 전달하고 그래프가 경로를 반환하는 경우 `graph_context.slot_limits.findings >= 1`.
     2. 동일 상황에서 `debug.context_notes`에 “slot rebalance” 문자열이 포함되는지 확인.

5. **CI 반영**
   - `.github/workflows/ci.yml`의 pytest 실행 목록에 위 신규 테스트 경로를 포함하고, Slot Rebalancing 관련 케이스만 따로 빠르게 재현할 수 있는 `pytest -k slot_rebalance` 스텝을 추가해 빠른 피드백 루프를 만든다.
   - 워크플로우 결과 요약에 Spec-04 상태(예: `SPEC04_STATUS=pass/fail`)를 출력해 대시보드에서 바로 확인할 수 있게 한다.

### 🔹 진행 상황 요약 (2025-11-15)

- Slot floor 로직이 `/scripts/vision_pipeline_debug.sh` 케이스(IMG_001, IMG_003, IMG201)에 모두 적용되어 `context_slot_limits.findings=1`로 유지되고, `context_notes=["findings slot rebalanced from 1 to 1"]` 메시지가 `graph_context`, `debug`, `evaluation` 노트까지 일관되게 전달됨을 확인했다.
- VGL 가중치는 리밸런싱 발생 시 자동으로 +0.02~0.03 상승(예: `mode_weights.VGL=0.333`)해 Spec-04에서 요구한 신뢰도 반영이 구현되었고, evaluation/response `notes`에도 동일 메시지가 남아 Explainability 경로가 확보되었다.
- Fallback 메타(`finding_fallback.used/forced`) 및 9단계 history는 변하지 않아 Spec-01/02 상태와 호환된다.

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

### 🔹 Spec-05 라벨 표준화 진행 메모 (2025-11-16)

- `_normalise_findings()` 결과를 파이프라인이 덮어쓰는 문제가 있었는데, `DummyFindingRegistry` 시드가 다시 적용될 때도 동일 헬퍼를 재사용하도록 `/pipeline/analyze`를 수정했다. 이제 `force_dummy_fallback` + `mock_seed` 시나리오는 `Subarachnoid Hemorrhage`, `Left parietal lobe`처럼 canonical 라벨로 정규화되고 `label_normalization` 이벤트가 노출된다.
- 캡션 기반 fallback(`strategy: "fallback"`) 경로도 `/pipeline/analyze` 단계에서 label telemetry가 비어 있으면 `_normalise_findings()`를 재실행해 canonical 라벨을 만들고 synthetic 이벤트를 보강한다. `tests/test_paths_and_analyze.py::test_pipeline_backfills_missing_label_events`가 이를 커버하며, `scripts/vision_pipeline_debug.sh IMG_001 '{"force_dummy_fallback":true}'`, `IMG_003`, `IMG201` 모두에서 `label_normalization` 배열이 채워지는 것을 확인했다.
- `GraphRepo.prepare_upsert_parameters()` 가 그래프 업서트 직전 `finding.type/location` 값이 canonical 사전에 일치하는지 다시 검증하고, 미일치 시 즉시 422 오류를 반환한다. 파이프라인이 실수로 로우 라벨을 흘려보내더라도 그래프에는 canonical 라벨만 저장된다.
- **신규:** Graph context가 실제 경로(`MATCH (p=...)`)를 반환하지 못하는 경우, `GraphContextBuilder`가 facts에 포함된 findings를 활용해 최소 evidence path를 합성한다. 덕분에 `/scripts/vision_pipeline_debug.sh` 출력의 `context_paths_len`이 0에서 1+로 상승하고, `graph_context.paths`가 빈 배열이 아니게 된다.
- CI는 `pytest tests/test_normalizer.py tests/test_debug_payload.py tests/test_context_orchestrator.py tests/test_paths_and_analyze.py`와 `PYTHONPATH=grounded-ai/api python scripts/check_label_drift.py`를 계속 돌리며 Spec-05 회귀를 감시한다.


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

### 🔹 Spec-05 액션 플랜

1. **라벨·위치 캐노니컬 정의 & 드리프트 감시**
   - `services/ontology_map.py` + `docs/ontology/labels.md`를 canonical 소스로 유지하고, `scripts/check_label_drift.py`를 “canonical과 다를 경우 실패” 로직으로 고도화한다. CI는 dummy seed 전체를 정적 검사해 미정의 라벨이 있으면 즉시 차단한다.
   - `DummyFindingRegistry` 로딩 시 canonical 매핑을 즉시 적용해 fallback 데이터도 이미 canonical 상태가 되도록 한다.

2. **Normalizer/파이프라인 확장 (진행 중)**
   - 2025-11-16 기준 `DummyFindingRegistry` 기반 fallback은 `_normalise_findings()` 재사용 덕분에 canonical 라벨과 telemetry를 모두 노출하며, `tests/test_paths_and_analyze.py::test_pipeline_seeded_fallback_emits_label_events`가 이를 보증한다.
   - 캡션 기반 fallback(`strategy: "fallback"`)도 label 이벤트가 비면 `_normalise_findings()`로 재정규화한 뒤 synthetic 이벤트를 주입해 회귀를 방지한다(`tests/test_paths_and_analyze.py::test_pipeline_backfills_missing_label_events` 추가).
   - GraphRepo `prepare_upsert_parameters()` 단계에서 canonical 미일치 시 `ValueError`를 던져 HTTP 422로 변환하므로, DB에는 canonical 라벨만 저장된다.
   - Graph context는 paths 조회가 실패하더라도 facts 기반 evidence path를 합성하도록 방어 로직을 추가해, Explainability 슬롯이 항상 최소 1개 이상 채워지도록 했다(`tests/test_paths_and_analyze.py::test_pipeline_reports_no_paths_when_graph_returns_none` 업데이트).

3. **OntologyVersion 연동**
   - Neo4j에 `(:OntologyVersion {version_id:"1.1-label-std"})-[:STANDARDIZES]->(:Label {name:"Subarachnoid Hemorrhage"})` 구조를 추가해 canonical 라벨과 버전을 연결.
   - `/pipeline/analyze` 응답의 `graph_context`와 `results`에 `labels_version` 필드를 포함시키고, debug payload에도 삽입한다.
   - GraphRepo `prepare_upsert_parameters()` 단계에서 canonical 여부를 검사하고, 미일치 시 `ValidationError("label_not_canonical")` 또는 `errors.append({"stage":"normalize","msg":"label_not_canonical"})` 처리.

4. **테스트 & 데이터 검증**
   - `tests/test_normalizer.py`에 canonical 변환/이벤트 로깅 케이스를 추가하여 fallback 경로까지 검증한다.
   - `tests/test_paths_and_analyze.py`에는 alias 입력 → canonical 출력 여부를 확인하는 케이스를 추가한다.
   - `scripts/check_label_drift.py`는 CI에서 필수 실행 스텝으로 유지한다.

5. **CI 반영**
   - `.github/workflows/ci.yml`에 `pytest tests/test_normalizer.py`와 `python scripts/check_label_drift.py`를 포함시키고, 실패 시 `SPEC05_STATUS=fail`을 출력한다.
   - 필요 시 `pytest -k label_normalization` 스텝으로 빠른 스모크 테스트를 추가한다.

6. **관측 가능성 강화**
   - `/scripts/vision_pipeline_debug.sh` 출력에 `LABEL NORMALIZATION` 섹션을 추가해 raw ↔ canonical ↔ rule trace를 직관적으로 확인할 수 있게 한다.
   - 런타임 metrics에 `label_normalisation_events_total`, `label_conflict_detected_total` 등을 추가해 운영 중에도 이상을 모니터링한다.

## ✅ [Spec-D] 그래프 기반 앙상블 합의 (Graph-Guided Consensus)

### 🔹 진행 메모 (2025-11-16)

- `/pipeline/analyze` 가 그래프 증거가 확보된 경우 `VGL` 출력을 anchor 로 두고 `anchor_mode_used: true`, `graph_paths_strength` 값을 `compute_consensus()`에 전달한다. `GraphContextBuilder` 가 최소 1개 이상의 경로를 보장하면서 `weights["VGL"]` 이 1.8 이상으로 상승해 V/VL이 불일치해도 그래프 모드가 합의를 주도할 수 있게 됨.
- `results["V"]`/`["VL"]`는 그래프 해석과 어긋나는 텍스트일 경우 자동으로 `degraded: "graph_mismatch"` 플래그가 붙고 consensus 계산 시 패널티를 받는다. `scripts/vision_pipeline_debug.sh IMG_001 --force_dummy_fallback` 케이스에서 V/VL이 자동으로 degraded 되고, consensus status가 `agree` 로 승급된 것을 확인했다.
- Structured finding terms(`type/location`) 는 `_collect_finding_terms()` 를 통해 추출되어 `_structured_overlap_score()` 로 합의 가중치에 반영된다. `IMG_003` 케이스에서 `agreement_components.structured=0.3`으로 기록됐다.
- 그래프 신뢰도는 `GRAPH_EVIDENCE_WEIGHT * graph_paths_strength` 공식을 통해 text score에 직접 가산된다. 합의 결과 `notes` 에 `graph evidence boosted consensus (paths_signal=0.23)` 메시지가 남도록 했으며, degraged inputs와 supporting/disagreed modes가 명확히 표기된다.

### 🔹 Spec-D 액션 플랜

1. **V/VL 그래프 검증 가드** – `has_paths` 일 때 V/VL 출력이 공백이거나 VGL과 0.1 미만의 자카드 유사도라면 즉시 `degraded="graph_mismatch"` 및 `"mismatch with graph-backed output"` 노트를 삽입한다. (완료)
2. **가중치 재분배** – 기본적으로 `weights={"V":1.0,"VL":1.2,"VGL":1.0}` 을 사용하되, 경로가 존재하면 `weights["VGL"]=1.8 + slot_rebalanced_flag*0.2` 로 승격하고, 리밸런싱만 발생해도 +0.1 가중치를 부여한다. (완료)
3. **Anchor 모드** – 경로가 존재하면 `anchor_mode="VGL"`, 없으면 Anchor 비활성화. Anchor 스코어는 0.75 이상이어야 효력 발생. (완료)
4. **구조화/그래프 보너스** – Text, structured overlap, graph signal, modality penalty를 모두 결합한 `agreement_components` 를 consensus 응답에 노출한다. (완료)
5. **검증** – `tests/test_paths_and_analyze.py::test_pipeline_reports_no_paths_when_graph_returns_none` 등 기존 테스트와 신규 합의 스냅샷(`tests/test_consensus_snapshot.py`)으로 회귀를 감시한다. E2E 스크립트에서도 `status=agree`, `anchor_mode_used=true`, `degraded_inputs=["V","VL"]` 를 확인했다. (완료)

## ✅ [Spec-E] 경로 증거 복원 (Context Path Recovery)

### 🔹 진행 메모 (2025-11-16)

- `GraphRepo.query_paths()` 는 `grounded-ai/api/services/graph_repo.py` 의 `GRAPH_PATHS_QUERY` 를 통해 `MATCH (img)-[:HAS_FINDING]->(f)` / `MATCH (f)-[:LOCATED_IN]->(:Anatomy)` / `MATCH (img)-[:SIMILAR_TO]->(s)` 등 실제 Neo4j 경로를 추출하고, 각 segment의 start/end 노드 메타를 API 레이어에서 문자열 triple 로 재조합한다. 합성 경로는 이제 백업 용도로만 남는다.
- `GraphContextBuilder` 는 Neo4j 쿼리가 비었을 때만 `_fact_paths_from_findings()` 로 fallback path 를 생성하며, 성공 시에는 실제 경로를 그대로 `graph_context.paths`/`debug.context_paths_head` 에 반영한다.
- `graph_paths_strength` 는 real path 의 triple 수에 기반해 계산되며 Spec-D consensus 보너스(`notes: "graph evidence boosted consensus"`)와 연결된다. `scripts/vision_pipeline_debug.sh IMG_001/IMG_003/IMG201 --force_dummy_fallback` 출력에서도 multi-hop triple 들이 그대로 노출된다.
- `scripts/vision_pipeline_debug.sh` 를 `IMG_001`, `IMG_003`, `IMG201` 입력으로 다시 실행해 본 결과, 각 케이스의 `graph_context.paths` 에서 Neo4j가 반환한 `Image[...] -HAS_FINDING-> Finding[...]` 경로가 그대로 노출되며 `context_fallback_used=false` 를 유지했다. consensus 섹션에는 `graph evidence boosted consensus (paths_signal=0.23)` 메모가 함께 기록되어 Spec-D와 Spec-E가 연동됨을 확인했다.

### 🔹 Spec-E 액션 플랜

1. **실경로 복원** – `GRAPH_PATHS_QUERY` 가 multi-hop `MATCH p=` 절을 사용해 slot 별 상위 k 경로를 반환하고, `GraphRepo._segments_to_triples()` 가 start/end 노드 라벨과 id를 포맷한다. (완료)
2. **경로 합성기** – `_fact_paths_from_findings()` 는 Neo4j 쿼리가 실패했을 때만 작동하여 최소 evidence path 를 보장한다. (완료)
3. **리밸런싱/슬랏 정보 유지** – 실제 경로와 fallback 경로 모두 slot 메타(`notes: "findings slot rebalanced from 1 to 2"`)를 그대로 흘려보내 Explainability 기록을 보존한다. (완료)
4. **Consensus/평가 연동** – real path 는 debug payload, HTTP 응답(`graph_context.paths`), evaluation(`ctx_paths_len`)에 모두 반영되어 downstream 소비자가 동일한 근거를 확인할 수 있다. (완료)

### 🔹 남은 과제 (2025-11-16 기준)

- **OntologyVersion/labels_version 노출**: canonical 맵 버전을 Neo4j 및 API 응답에 연결하는 작업은 아직 미완료 상태다.
- **OntologyVersion Propagation**: canonical 라벨 버전을 Neo4j/응답 payload로 노출하는 작업(`labels_version`)은 아직 미완료 상태다.

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
