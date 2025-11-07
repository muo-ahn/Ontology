# Vision Pipeline Debug — Confirmed Problem Scope (v0.1)

본 문서는 현재 로그 기반으로 **추정 없이 확정할 수 있는 문제 범위(scope)**만을 정리한 것이다.
이 문서는 이후 **Spec-Driven Development**를 위한 기반 문서로 사용된다.
(= 여기 적힌 것들은 “고쳐야 할 가능성”이 아니라 “이미 망가져 있는/일관되지 않은 상태로 확인된 것들”)

---

## 1. Context

- 대상: `vision_pipeline_debug.sh` 및 `/pipeline/analyze` 관련 Vision → Graph → Context 파이프라인
- 주요 테스트:

  - `IMG201` (Ultrasound fatty liver)
  - `IMG_001` (Acute fatty liver CT)

- 공통 패턴:

  - `dummy_lookup` 정상 작동
  - similarity 계산 일부 정상 작동
  - graph summary 상 edge는 존재
  - 그러나 slot, path, provenance, fallback, consensus 등 상위 메타 정보가 **실제 동작과 불일치**

이 문서의 범위는 **“현재 main 기준 시스템의 관측 가능한 일관성 위반”**이다.

---

## 2. Goals (for this scope doc)

1. 테스트 로그로 **사실상 확정된 문제만** 식별한다.
2. 각 문제를 ID로 명시해, 이후 별도 Spec/Issue/Ticket으로 분해 가능하게 만든다.
3. 구현 논의(어떻게 고칠지)는 다음 단계의 스펙 문서에서 다루며, 여기서는 **무조건 “현 상태에서 잘못된 점”만 기록**한다.

---

## 3. Confirmed Issues

### S01. SlotLimit / Context 불일치

**현상**

- 여러 실행에서 공통적으로:

  - `context_findings_len > 0`
  - `graph_context.facts.findings`에도 유효한 finding들이 포함됨.
  - 동시에:

    ```json
    "context_slot_limits": { "findings": 0, "reports": 1, "similarity": 1 }
    "slot_limits": { "findings": 0, "reports": 1, "similarity": 1 }
    ```

- 즉, “findings 슬롯 0”이라고 보고하면서 실제로는 findings를 반환하고 있음.

**문제**

- 디버그/메타 정보가 실제 동작과 모순됨.
- Downstream/분석/논문 관점에서:

  - “그래프 findings를 사용 안 한다”라고 오해하게 만드는 잘못된 시그널.

- **확정 결론**: SlotLimit 출력 로직 또는 노출 포맷이 현재 상태 기준으로 잘못되어 있음 (신뢰 불가).

---

### S02. Evidence Path 미노출

**현상**

- Summary:

  - `HAS_FINDING`, `LOCATED_IN`, `RELATED_TO`, `DESCRIBED_BY`, `SIMILAR_TO` 등 edge 카운트와 평균 conf는 존재.
  - 일부 케이스에서 `similarity_edges_created > 0`.

- 그러나 항상:

  ```json
  "context_paths_len": 0,
  "context_paths_head": [],
  "context_paths_triple_total": 0,
  "triples": "[EDGE SUMMARY]\n...\n[EVIDENCE PATHS (Top-k)]\n데이터 없음"
  ```

**문제**

- edge는 존재하는데, evidence path는 전혀 surface되지 않음.
- Graph 기반 설명 가능성(“왜 이런 결과가 나왔는가”)이 완전히 부재.
- **확정 결론**: 현재 main 기준, evidence path 출력은 비어 있거나 연결되지 않은 상태로, 설계 의도(그래프 활용)와 불일치.

---

### S03. Upsert 실패 처리 (특히 IMG_001)

**현상 (IMG_001)**

- `pre_upsert_findings_len > 0` (정규화된 findings 존재).
- `upsert_receipt.finding_ids: []`
- E2E 응답의 `errors`:

  ```json
  {
    "stage": "upsert",
    "msg": "normalized findings present but upsert returned no finding_ids"
  }
  ```

- 최종 `graph_context.facts.findings`는 빈 배열.
- 상위 결과/consensus는 `"데이터가 없습니다.(FACTS JSON)"`를 사용자에게 노출.

**문제**

- 시스템이 “정규화 → upsert 실패”를 인지했으나,

  - 그 상태를 안전하게 처리하지 않고 “진짜 데이터 없음”으로 오도.

- 의료 컨텍스트 기준, 이는 UX/신뢰성 측면에서 명백한 문제.

**확정 결론**

- `normalized findings 존재 + finding_ids 없음` 상황에서:

  - 에러 기록과 최종 응답 메시지가 논리적으로 일관되지 않음.
  - ingest 실패와 실제 임상 결론이 구분되지 않는다.

---

### S04. Provenance 메타데이터 불일치 (finding_fallback, finding_source, seeded_ids)

**현상**

- 여러 실행에서:

  - `finding_source = null`, `seeded_finding_ids = []`로 표기되지만,
  - 실제 반환된 findings에는 mock/seed 기반 ID들이 포함되어 있음.

- `finding_fallback`:

  - 일부 경우 항상 `used=false`, `seeded_ids_head=[]`로 유지.
  - force 옵션 사용 시나리오에서도 값이 null 또는 기본값으로 깨짐.

**문제**

- 어떤 finding이:

  - VLM 기반인지,
  - mock seed인지,
  - fallback registry 기반인지,
  - 실제 로그만 보고 신뢰할 수 없음.

- 재현성, 성능 분석, 논문 작성 모두에 치명적.

**확정 결론**

- Provenance 관련 필드들은 현재 main 기준으로 **실제 파이프라인 상태를 올바르게 반영하지 않는다.**
- 디버그/분석용 메타데이터로 신뢰 불가 상태.

---

### S05. force_dummy_fallback 파라미터 / 스크립트 계약 문제

**현상**

- `vision_pipeline_debug.sh` 호출:

  ```bash
  "./scripts/vision_pipeline_debug.sh" "..." "{"force_dummy_fallback": true}"
  ```

- [10-1]에서:

  ```json
  "JSON decode error" / "Expecting property name enclosed in double quotes"
  ```

- 해당 실행에서:

  - `finding_fallback.forced`는 항상 `false` 또는 `null`.

**문제**

- 스크립트가 유효한 JSON body를 만들지 못하고 있음.
- 결과적으로 `force_dummy_fallback` 기능 테스트 불가.
- 로그상 “강제 옵션 있음”처럼 보이지만 실제로는 단 한 번도 정상 동작하지 않는 상태.

**확정 결론**

- force_dummy_fallback 관련 **클라이언트-서버 계약이 깨져 있음**.
- 관련 디버그 시나리오는 현재 전부 무효.

---

### S06. 동일 케이스 간 Debug 스냅샷 불일치

**현상**

- 같은 `IMG201`에 대해:

  - [8] debug, [10-1] debug 각각에서 `pre_upsert_findings_head`와 seed 구성 상이.
  - 실행마다 다른 dummy/seed 조합이 나타나며, 어떤 것이 실제 파이프라인 기준인지 구분 불가.

**문제**

- 동일 입력에 대해 서로 다른 internal snapshot이 노출됨.
- random/dummy라 하더라도:

  - 어느 출력이 “권위 있는 진실인지” 구분할 수 없어 디버깅/실험 해석에 혼선을 줌.

**확정 결론**

- 현재 debug 출력은 **단일 진실 소스가 아니라, 서로 다른 경로/시점의 상태를 혼합해 보여주고 있다.**
- Spec-Driven 관점에서 “debug 응답은 파이프라인 단일 실행의 일관된 상태를 표현해야 한다”는 요구사항을 만족하지 못함.

---

### S07. Consensus 모듈의 실질적 무력화

**현상**

- 모든 케이스:

  - `status: "disagree"`
  - `confidence: "low"`
  - `notes: "outputs diverged across modes"`

- 추가 테스트에서도:

  - `agreement_score`는 0 또는 0에 매우 근접한 값.
  - 결과적으로 사용자에게 항상 “낮은 확신 / 불일치”만 전달.

**문제**

- 그래프 evidence도 노출되지 않는 상황에서,

  - consensus 계층은 시스템 신뢰도를 올리지 못하고, 단지 불일치 메시지만 양산.

- Spec 상 “멀티 모드/멀티 에이전트 합의”가 목표였다면,

  - 현 상태는 그 목표와 명백히 어긋남.

**확정 결론**

- 현재 구현 상태 기준, Consensus 모듈은 기능적으로 유의미한 합의를 제공하지 못하고 있음.
- 적어도 “이 상태 그대로 논문/제품의 강점으로 주장하기는 어렵다”는 것이 로그로 확인됨.

---

## 4. This Scope Doc Guarantees

이 문서에 적힌 항목들은 다음을 전제로 한다:

- 모두 **실제 로그 출력만으로 확인 가능한 불일치/오류**에 한한다.
- “어떻게 고칠지”는 포함하지 않는다. (이후 `spec_*.md`에서 다룸)
- 각 Sxx 이슈는:

  - 별도 이슈/PR/테스트 케이스로 분해 가능하도록 독립적으로 기술했다.
  - 추후 스펙 문서에서:

    - Expected Behavior
    - Interface Contract
    - Test Scenario (curl + jq)
    - Migration/Backward-compat Note
      를 붙여가며 구체화할 수 있다.
