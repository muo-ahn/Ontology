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

---

## S05. force_dummy_fallback 파라미터

### ✅ Expected Behavior

- 클라이언트(JSON body)에서 다음과 같이 호출 가능해야 함:

  ```bash
  curl -X POST ... -d '{"file_path":"...","force_dummy_fallback":true}'
  ```

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

  - JSON decode error 제거.
  - [8] 블록의 `forced: true` 정상 출력.

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

---

## S07. Consensus 모듈 개선

### ✅ Expected Behavior

- 합의 스코어(`agreement_score`)가 0.0~1.0 사이에서 유효하게 분포해야 함.
- 모든 경우 `status=disagree`만 나오는 것은 비정상.
- Graph evidence가 포함될 경우 합의에 반영되어야 함.

### 💡 Implementation Spec

- 파일: `services/evaluation.py`
- 개선:

  - 텍스트 유사도 + type/location overlap 가중치 기반 합의 스코어 재산정.
  - threshold 완화:

    ```python
    status = "agree" if agreement_score > 0.35 else "disagree"
    ```

  - Graph evidence가 존재할 경우 bonus weight 추가.

- Test:

  - 최소 1개 케이스에서 `status=agree`, `confidence=medium` 이상 확인.

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

1. **각 Spec(S01–S07)**을 독립적인 테스트 케이스 단위로 분리.
2. 각 항목에 대한 `spec_*.md` 세부 설계 작성:

   - Expected
   - Failure Condition
   - Test Curl
   - Regression Criteria

3. 이후 `vision_pipeline_debug.sh` 자동 회귀 테스트 루틴 추가:

   ```bash
   ./scripts/test_pipeline_integrity.sh --case IMG201 --expect slots,paths,consensus
   ```
