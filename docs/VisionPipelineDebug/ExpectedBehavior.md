# Vision Pipeline Spec — Expected Behavior (v0.2)

본 문서는 `Vision → Graph → Context` 파이프라인의 **정상 동작 기준 (Spec-Driven Development용)**을 정의한다.
각 항목은 `S01–S07` confirmed issue 대응이며,
테스트 시 **현재 로그와 비교하여 통과 조건이 명확히 판별될 수 있도록 작성**되었다.

---

## S01. SlotLimit / Context 불일치

### ✅ Expected Behavior

- `context_slot_limits.findings` 값은 실제 `context_findings_len`에 기반해야 한다.
- 최소 1개 이상의 finding이 존재하면:

  ```json
  "context_slot_limits": { "findings": 1, "reports": 1, "similarity": 1 }
  ```

  혹은 `findings >= 1` 형태로 표시되어야 한다.

- Slot allocator가 동적 재분배를 수행하더라도, **최종 context summary와 일관되어야 함.**

### 💡 Implementation Spec

- 파일: `api/services/context_pack.py`
- 함수 후보: `_rebalance_slots` 또는 `_allocate_slots`
- 조건 추가:

  ```python
  if context.findings and slot_limits['findings'] == 0:
      slot_limits['findings'] = min(len(context.findings), k_default)
  ```

- unit test:

  - 조건: `context_findings_len > 0`
  - 기대값: slot_limits.findings ≥ 1

### ✅ Verification (2025-11-08)

- `python -m pytest tests/test_paths_and_analyze.py -k slot_limits_keep_findings_when_summary_has_findings`
  - harness 기반 케이스에서 `debug.context_slot_limits.findings >= 1` ??검증 완료.
- `./scripts/vision_pipeline_debug.sh "/data/medical_dummy/images/api_test_data/Ultrasound-fatty-liver-Ultrasound-of-the-whole-abdomen-showing-increased-hepatic.png" "{}"`
  - 실 서비스 IMG201 런??`context_slot_limits: {"findings":1,"reports":1,"similarity":0}` 로그로 교차 확인.
- findings??존재하지 않는 XR/CT 케이스(예: IMG_001, IMG_003)는 `context_findings_len=0` → spec에 따라 findings 슬롯 0 유지.

---

## S02. Evidence Path 미노출

### ✅ Expected Behavior

- `context_paths_len > 0`이면 evidence path가 최소 1개 이상 포함되어야 한다.
- summary의 edge count와 path count가 모순되면 안 된다.
- `triples` 내 `[EVIDENCE PATHS]` 섹션이 항상 비어 있는 경우는 버그로 간주한다.

### 💡 Implementation Spec

- 파일: `api/services/context_pack.py`
- 함수: `_build_context_paths`
- 조건:

  - Graph query에서 가져온 edge triple들을 Top-k 기준으로 path 변환 후 JSON 직렬화.
  - 비어 있을 경우라도 `"데이터 없음"` 대신 `"No path generated (0/Top-k)"` 등 명시적 표시.

- Test Case:

  - Dummy Graph에 최소 2-step edge 존재 시 `context_paths_len >= 1`.

### ✅ Verification (2025-11-08)

- `python -m pytest tests/test_paths_and_analyze.py -k builds_fallback_paths_when_graph_returns_none`
  - repo path 조회 결과가 비어도 fallback evidence path가 생성되어 `context_paths_len > 0`이 보장됨.
- `./scripts/vision_pipeline_debug.sh "/data/medical_dummy/images/api_test_data/Ultrasound-fatty-liver-Ultrasound-of-the-whole-abdomen-showing-increased-hepatic.png" "{}"`
  - 실 런타임(IMG201)에서 `[EVIDENCE PATHS (Top-k)]`가 실제 path를 surface 함.
- `./scripts/vision_pipeline_debug.sh "/data/medical_dummy/images/api_test_data/Acute-fatty-liver-of-pregnancy-non-contrast-computed-tomography-Non-contrast-computed.png" "{}"`
  - 그래프 evidence가 전무한 CT 케이스에서는 `[EVIDENCE PATHS]`에 `No path generated (0/2)` 메시지가 출력되어 빈 상태가 명시됨.

---

## S03. Upsert 실패 처리

### ✅ Expected Behavior

- `normalized_findings_len > 0` AND `finding_ids == []` → 반드시 `error` 레벨 로그 및 응답에 명시.
- 사용자-facing 응답:

  - `"status": "degraded"`
  - `"notes": "graph upsert failed, fallback used"`
  - `"facts"`는 fallback/normalized 기반 최소 결과 포함.

### 💡 Implementation Spec

- 파일: `api/services/upsert_repo.py`
- 로직 추가:

  ```python
  if normalized_findings and not finding_ids:
      logger.error("Graph upsert failed: normalized findings present but no ids returned")
      return {"status": "degraded", "fallback_used": True}
  ```

- Test:

  - IMG_001 케이스 재실행 시 `"status": "degraded"` 표시되어야 함.
  - `데이터가 없습니다.` 문구 금지.

### ✅ Verification (2025-11-08)

- `python -m pytest tests/test_paths_and_analyze.py -k marks_degraded_when_upsert_returns_no_ids`
  - upsert가 finding ID를 반환하지 못하면 `status="degraded"`와 fallback note가 노출됨을 단위 테스트로 보장.
- `./scripts/vision_pipeline_debug.sh "/data/medical_dummy/images/api_test_data/Acute-fatty-liver-of-pregnancy-non-contrast-computed-tomography-Non-contrast-computed.png" "{}"`
  - 실 런에서 `status":"degraded"`, `notes:"graph upsert failed, fallback used"`가 응답/평가 블록에 포함되고 `graph_context.facts.findings`가 정규화 finding 으로 채워짐을 확인.

---

## S04. Provenance 메타데이터 일관성

### ✅ Expected Behavior

- 모든 응답에는 다음 필드가 일관되어야 함:

  ```json
  "finding_source": "mock_seed" | "vlm" | "registry" | "fallback"
  "seeded_finding_ids": [...]
  "finding_fallback": { "used": true/false, "strategy": "...", "forced": ... }
  ```

- 동일 이미지(`image_id`)에 대해 [8], [9], [10-1] 모두 동일한 값을 반환해야 함.

### 💡 Implementation Spec

- 중앙 관리 구조 도입:

  ```python
  context_meta = FindingProvenance(...)
  ```

  → 이후 모든 stage에서 참조만.

- 코드 수정 대상:

  - `routers/pipeline.py` (normalize 이후)
  - `services/context_pack.py` (rebalance 및 evaluation 전)

- Test:

- `force_dummy_fallback=false`일 때 used=False.
- 동일 run 내 모든 endpoint 결과에서 값이 일치해야 함.

### ✅ Verification (2025-11-08)

- `python -m pytest tests/test_paths_and_analyze.py -k provenance_metadata_aligns_across_sections`
  - graph_context, results, evaluation, debug 전 구간에서 `finding_source`, `seeded_finding_ids`, `finding_fallback`이 동일하게 노출됨을 보장.
- `./scripts/vision_pipeline_debug.sh "/data/medical_dummy/images/api_test_data/Ultrasound-fatty-liver-Ultrasound-of-the-whole-abdomen-showing-increased-hepatic.png" '{"force_dummy_fallback": true}'`
  - fallback 강제 시 [8]/[9]/[10-1] 모든 블록에서 `finding_source:"mock_seed"`, `seeded_finding_ids`, `finding_fallback.used=true`가 일치함을 확인.
- `./scripts/vision_pipeline_debug.sh "/data/medical_dummy/images/api_test_data/Acute-fatty-liver-of-pregnancy-non-contrast-computed-tomography-Non-contrast-computed.png" "{}"`
  - 그래프 degraded 경로에서도 `finding_source:"vlm"`, `finding_fallback.used=false`, `finding_provenance` 값이 graph_context/results/evaluation/debug 전 구간에 동일하게 나타남을 확인.

---

## S05. force_dummy_fallback 파라미터

### ✅ Expected Behavior

- 클라이언트(JSON body)에서 다음과 같이 호출 가능해야 함:

  ```bash
  curl -X POST ... -d '{"file_path":"...","force_dummy_fallback":true}'
  ```

  ↳ Bash에서는 JSON 전체를 `'...'`로 감싸거나 내부 따옴표를 escape 해야 함.

- 서버에서:

  - `finding_fallback.forced: true`
  - 실제 fallback 경로 실행.

- 잘못된 JSON 파싱 에러는 발생하지 않아야 함.

### 💡 Implementation Spec

- 파일:

  - `scripts/vision_pipeline_debug.sh` (client)
  - `routers/pipeline.py` (server)

- 수정 방향:

  - Bash에서 body escape:

    ```bash
    jq -n --arg path "$1" --argjson params "$2" '{file_path:$path} + $params'
    ```

  - FastAPI Router에서 `force_dummy_fallback: Optional[bool] = False` 파라미터 명시.

- Test:

  - JSON decode error 제거 (jq 기반 shell 테스트 포함).
  - [8] 블록의 `forced: true` 정상 출력 & [10-1] 응답에도 반영됨.

### ✅ Verification (2025-11-08)

- `python -m pytest tests/test_paths_and_analyze.py -k provenance_metadata_aligns_across_sections` (force_dummy_fallback 시)
  - fallback 강제 시 `finding_fallback.used=true`와 `finding_source:"mock_seed"`가 graph_context/results/evaluation/debug 전 구간에 존재함을 확인.
- `./scripts/vision_pipeline_debug.sh "/data/medical_dummy/images/api_test_data/Ultrasound-fatty-liver-Ultrasound-of-the-whole-abdomen-showing-increased-hepatic.png" '{"force_dummy_fallback": true}'`
  - JSON 인코딩 오류 없이 요청 수락, [8]/[9]/[10-1] 모두에서 `finding_fallback.forced:true` 노출.

---

---

## S06. 동일 케이스 간 Debug 스냅샷 불일치

### ✅ Expected Behavior

- 동일 `image_id` 실행 시, [8], [9], [10-1] 모두 동일한 `pre_upsert_findings_head`를 가져야 함.
- random 또는 dummy seed라면 고정 seed를 명시적으로 지정해야 함:

  ```python
  random.seed(image_id)
  ```

### 💡 Implementation Spec

- 파일:

  - `services/normalizer.py`
  - `routers/pipeline.py`

- 수정:

  - dummy/mock generation 시 image_id 기반 deterministic seed 지정.

- Test:

  - 동일 이미지 3회 실행 시 `pre_upsert_findings_head` 완전 동일해야 함.

### ✅ Verification (2025-11-08)

- `./scripts/vision_pipeline_debug.sh "/data/medical_dummy/images/api_test_data/Ultrasound-fatty-liver-Ultrasound-of-the-whole-abdomen-showing-increased-hepatic.png" "{}"` 두 번 실행 → `[8]`과 `[10-1]`의 `pre_upsert_findings_head`가 동일하게 캐시됨.
- 같은 커맨드에 `{"force_dummy_fallback": true}`를 전달한 반복 실행에서도 seed 기반 findings 순서가 바뀌지 않음을 확인.

---

## S07. Consensus 모듈 개선

### ✅ Expected Behavior

- 합의 스코어(`agreement_score`)가 0.0~1.0 사이에서 유효하게 분포해야 함.
- 모든 경우 `status=disagree`만 나오는 것은 비정상.
- Graph evidence가 포함될 경우 합의에 반영되어야 함.
- 텍스트 유사도(60%) + type/location 구조적 정합성(30%) + graph bonus(10%)가 조합되어 스코어를 산출해야 함.
- VGL이 근거를 제공하면 `"graph evidence boosted consensus"` 노트가 surface 되어야 함.

### 💡 Implementation Spec

- 파일: `routers/pipeline.py`
- 개선 사항:

  - `compute_consensus()`에서 `_collect_finding_terms()` / `_structured_overlap_score()`를 통해 type/location 구조 신호를 취합.
  - `graph_paths_strength`(경로 수 + triple depth 기반 0~1 정규화)을 VGL pair에 bonus(최대 +0.1)로 반영.
  - supporting_modes가 정합 시 `graph evidence boosted consensus` 및 `structured finding terms ...` 노트를 notes에 추가.

- Test:

  - `tests/test_consensus.py::test_compute_consensus_graph_bonus_improves_agreement`
  - `tests/test_consensus.py::test_compute_consensus_structured_terms_raise_score`

### ✅ Verification (2025-11-08)

- `python -m pytest tests/test_consensus.py -k graph_bonus`  
  - graph bonus가 score를 끌어올리고 notes에 `"graph evidence boosted consensus"`가 기록됨.
- `python -m pytest tests/test_consensus.py -k structured_terms_raise_score`  
  - 구조적 type/location overlap이 없을 때는 `status=disagree`, 동일 텍스트라도 structured hints가 존재하면 `status=agree`, `agreement_score≈0.38`.
- `./scripts/vision_pipeline_debug.sh "/data/medical_dummy/images/api_test_data/Acute-fatty-liver-of-pregnancy-non-contrast-computed-tomography-Non-contrast-computed.png" '{"force_dummy_fallback": true}'`  
  - 강제 fallback CT 케이스(IMG_001)에서도 fallback evidence path가 생성되어 `context_paths_len=1`, `graph_paths_strength≈0.33`, `results.consensus={"status":"agree","agreement_score":0.75,"notes":"…graph evidence boosted consensus…"}`로 확인됨.
- `./scripts/vision_pipeline_debug.sh "/data/medical_dummy/images/api_test_data/Non-contrast-computed-tomography-head-hepatic-encephalopathy-Non-contrast-computed.png" '{"force_dummy_fallback": true}'`  
  - IMG_003 degraded 시나리오에서도 동일하게 합의가 `"agree"`로 표기되고 그래프 bonus 노트가 로그에 남는다.
- `./scripts/vision_pipeline_debug.sh "/data/medical_dummy/images/api_test_data/Ultrasound-fatty-liver-Ultrasound-of-the-whole-abdomen-showing-increased-hepatic.png" '{"force_dummy_fallback": true}'`  
  - 풍부한 그래프 신호가 존재하는 IMG201 런에서 `context_paths_len=1`, `graph_paths_strength≈0.43`, `results.consensus.agreement_score=0.75`, `confidence:"medium"`이 유지됨.


---

## S08. Image Identity 서비스 예외 처리 미비

### Expected Behavior

- `/pipeline/analyze` 는 어떤 입력이라도 `image_id`/`case_id` 를 결정하거나, 실패 시 사용자 친화적인 JSON 오류를 반환해야 한다.
- normalizer 가 image_id 를 채우지 못했고 Dummy registry alias/ID lookup 도 실패한 경우:
  - `identify_image()` 가 `ImageIdentityError(status_code=502, "unable to derive image identifier")` 를 발생시켜야 한다.
  - FastAPI 응답에는 NameError 등 내부 스택이 노출되면 안 된다.

### Repro (2025-11-11)

```bash
./scripts/vision_pipeline_debug.sh \
  "/data/medical_dummy/images/api_test_data/Ultrasound-fatty-liver-Ultrasound-of-the-whole-abdomen-showing-increased-hepatic.png" \
  '{"force_dummy_fallback": true}'
```

- `[8] Vision Pipeline Debug Query` 가 `null` 로 표시되고, sync 호출은 아래 오류를 리턴

```json
{"detail":{"ok":false,"errors":[{"stage":"vlm","msg":"name 'normalized_image_id' is not defined"}]}}
```

### Root Cause

- `services/image_identity.identify_image()` 리팩터 도입 후, normalizer + alias lookup 모두 miss 하면 `normalized_image_id` 지역 변수가 생성되지 않은 채 사용된다.
- Dummy registry 가 alias 를 찾지 못한 경우 filename 기반 slug 를 재시도하지 않아 NameError 가 노출된다.

### Remediation Plan

1. `identify_image()` 내부에서 `normalized_image_id = working_image.get("image_id")` 값을 항상 초기화하고, 모든 파생 로직 이후에도 값이 없으면 `ImageIdentityError` 로 종료.
2. Path slug fallback (예: 파일명 → `IMG_ULTRASOUND_...`) 을 추가해 alias miss 시에도 deterministic ID 확보.
3. `tests/test_image_identity.py` 에 alias miss + slug fallback 케이스, 그리고 “ID 미생성 시 ImageIdentityError 발생” 케이스를 추가.
4. 위 재현 스크립트를 다시 실행해 `case_id":"CASE_IMG_...` 형태로 정상 응답이 나오는지 검증.

### Status

- **해결 (2025-01-14)** — `services/image_identity.identify_image()` 는 slug fallback + 502 가드 적용 완료, `routers/pipeline.py` 의 fallback 로그에서도 존재하지 않는 `normalized_image_id` 대신 `image_id` 를 사용하도록 수정해 NameError 경로 제거.

### Verification (2025-01-14)

```bash
pytest tests/test_image_identity.py
./scripts/vision_pipeline_debug.sh "/data/medical_dummy/images/api_test_data/Ultrasound-fatty-liver-Ultrasound-of-the-whole-abdomen-showing-increased-hepatic.png" '{"force_dummy_fallback": true}'
```

- IMG201 케이스가 `case_id:"CASE_IMG201"` / `image_id:"IMG201"` 로 성공하고, fallback/seeded 컨텍스트 및 consensus 노트가 정상 표기됨을 확인.

---

## 5. Verification Plan

| Step | Command                                       | Expected Output                 | Related Spec |
| ---- | --------------------------------------------- | ------------------------------- | ------------ |
| 1    | `./scripts/vision_pipeline_debug.sh "IMG201"` | `slot_limits.findings >= 1`     | S01          |
| 2    | `grep "EVIDENCE PATHS" output.log`            | Path 목록 존재                  | S02          |
| 3    | `IMG_001` 실행                                | `"status": "degraded"`          | S03          |
| 4    | 동일 이미지 3회 실행                          | `pre_upsert_findings_head` 동일 | S06          |
| 5    | `force_dummy_fallback` 옵션 실행              | `finding_fallback.forced=true`  | S05          |
| 6    | Consensus 출력                                | 0 < agreement_score ≤ 1         | S07          |

---

## 6. Next Steps

1. **Spec 마이크로 테스트화:** S01~S08 각각을 독립 pytest/golden 케이스로 분리해 회귀 시 즉시 감지.
2. **spec_*.md 확장:** Expected / Failure / Repro Curl / Regression Criteria 템플릿을 문서화하고 TicketPlan 에 링크.
3. **자동 회귀 루틴:** `vision_pipeline_debug.sh` 호출을 모아둔 `scripts/test_pipeline_integrity.sh --case IMG201 --expect slots,paths,consensus` 스크립트화.
4. **CI 연동:** GitHub Actions 에서 slug fallback/IdentityError 테스트, graph-path guard, consensus snapshot 등을 nighty + PR 단계에서 실행.
