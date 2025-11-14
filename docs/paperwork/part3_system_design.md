# 📘 **Part III — 시스템 설계 (System Design)**

_Ontology-Grounded Vision-Language Reasoning Pipeline for Medical Imaging_
_Draft v1.0 (Based on main branch implementation)_

---

## **1. 전체 시스템 개요 (Overview)**

본 연구에서 구현한 **Ontology-Grounded Vision-Language Pipeline**은
의료 영상 입력을 그래프 기반 지식과 결합하여
설명가능한(reasoned) 진단 결과를 생성하는 end-to-end 시스템이다.

시스템은 다음 다섯 모듈로 구성된다.

| 모듈                | 주요 기능                          | 대표 클래스 / 파일           |
| ------------------- | ---------------------------------- | ---------------------------- |
| ① Vision Normalizer | 의료 영상 전처리 및 캡션 생성      | `services/vlm_normalizer.py` |
| ② Graph Repository  | Ontology 업서트 및 Path Query      | `services/graph_repo.py`     |
| ③ Context Builder   | Graph Context 구성 및 Path Scoring | `services/context_pack.py`   |
| ④ LLM Runner        | V/VL/VGL reasoning 수행            | `services/llm_runner.py`     |
| ⑤ Consensus Core    | 다중 모드 결과 통합 및 신뢰도 계산 | `services/consensus_core.py` |

전체 실행은 `routers/pipeline.py`의 `/pipeline/analyze` 라우트에서 일괄 수행된다.

---

## **2. 시스템 아키텍처**

### **2.1 파이프라인 흐름**

```text
Image File → Vision Normalizer → Graph Upsert
    ↓                         ↓
  Context Builder (Path + Slot)  → LLM(V, VL, VGL)
                                         ↓
                                Consensus Core
                                         ↓
                              Debug Payload / Output
```

### **2.2 주요 설계 특징**

- **비동기(async) 파이프라인**: FastAPI + asyncio 기반
- **Deterministic ID 정책**: image_id / version_id 고정
- **Graph-Aware Context**: multi-hop path evidence
- **Dynamic Slot Rebalancing**: shallow context 자동 보정
- **Weighted Consensus**: V/VL/VGL 결과 일관성 평가
- **Experiment Reproducibility**: seed registry + snapshot test

---

## **3. Vision Normalizer (영상 정규화)**

### **3.1 역할**

- 입력된 의료 영상을 VLM에 전달하여 caption을 생성하고,
  그 결과를 정규화(normalize)하여 downstream 모듈이 처리할 수 있도록 함.

### **3.2 주요 기능**

1. **이미지 ID 추출 (Deterministic Policy)**

   - 파일명에서 `IMG###` 패턴을 인식
   - 매핑 실패 시 alias lookup 수행
   - ID가 없을 경우 fallback ID 생성
     → 코드: `vlm_normalizer._derive_image_id()`

2. **시드 기반 Finding 매핑**

   - Dummy registry에서 사전 정의된 findings를 불러와 병합
   - `finding_fallback.source = "seeded"`로 표시

3. **mock-caption fallback 처리**

   - 의료 단어(keyword)가 포함되지 않은 캡션은
     `[mock-caption]` 태그와 함께 fallback으로 기록
   - `fallback_used`, `strategy`, `registry_hit` 플래그가 debug payload에 포함됨.

---

## **4. Graph Repository (Ontology Upsert Layer)**

### **4.1 역할**

영상에서 추출된 findings를 Neo4j Ontology로 업서트(upsert)하고,
이후 reasoning에 필요한 경로(path)를 탐색한다.

### **4.2 스키마 개요**

| 노드(Label) | 주요 속성          | 예시                     |
| ----------- | ------------------ | ------------------------ |
| `Image`     | image_id, modality | IMG_201, "CT"            |
| `Finding`   | label, score       | “Fatty Liver”, 0.92      |
| `Anatomy`   | label              | “Liver”                  |
| `Report`    | text               | “Increased echogenicity” |

관계(Relationships):

```
(Image)-[:HAS_FINDING]->(Finding)
(Finding)-[:LOCATED_IN]->(Anatomy)
(Finding)-[:RELATED_TO]->(Finding)
(Image)-[:DESCRIBED_BY]->(Report)
```

### **4.3 Upsert Query**

`UPSERT_CASE_QUERY`는 다음 과정을 수행한다.

1. Image 노드 생성 (image_id 기준 unique)
2. Finding 노드 병합 및 관계 연결
3. Report 노드 연결
4. Path-level triple 생성

→ 코드: `services/graph_repo.py` 내부 Cypher query

### **4.4 Path Query (Evidence Search)**

`GRAPH_PATHS_QUERY`는 다음을 수행한다.

1. 입력 이미지 ID 기준으로 관련 Findings 탐색
2. Findings에서 연결된 Anatomy, Report, Similar Findings 탐색
3. 경로(path)를 triple 형태로 직렬화
4. 각 path에 가중치(score)를 부여

반환 형식 예시:

```json
{
  "paths": [
    {
      "label": "Fatty Liver",
      "score": 0.82,
      "triples": [
        { "source": "Image", "rel": "HAS_FINDING", "target": "Fatty Liver" },
        { "source": "Fatty Liver", "rel": "LOCATED_IN", "target": "Liver" }
      ]
    }
  ]
}
```

---

## **5. Graph Context Builder (핵심 모듈)**

### **5.1 역할**

그래프에서 반환된 path들을 기반으로
LLM이 이해할 수 있는 구조적 context를 구성한다.

- Slot-based context allocation
- Path scoring & normalization
- Fallback path synthesis
- Context metadata 생성

→ 구현: `services/context_pack.py`

---

### **5.2 Slot 구조**

```json
{
  "findings": 1,
  "reports": 1,
  "similarity": 0
}
```

각 slot은 한정된 token budget 내에서 evidence를 배분하기 위한 논리 단위이다.

---

### **5.3 Slot Rebalancing 알고리즘**

문제: findings slot이 일시적으로 empty가 되면 context 전체가 shallow해짐.
해결책: `_rebalance_slot_limits()`와 `_ensure_finding_slot_floor()` 구현.

**핵심 로직:**

```python
if not hits.findings:
    findings_miss_count += 1
    if findings_miss_count < 2:
        # 최소 한 번은 findings slot 유지
        limits["findings"] = 1
else:
    findings_miss_count = 0
```

→ 결과적으로 findings slot이 0으로 고정되는 일이 발생하지 않음.

---

### **5.4 Path Scoring 및 Fallback**

- 각 경로는 segment score를 계산하여
  path_strength를 산출한다.
- path가 존재하지 않으면 `_build_fallback_path_rows()`를 통해
  최소 evidence를 생성한다.
- context_meta에는 각 slot의 사용량, 경로 수, rebalance 기록이 저장된다.

---

### **5.5 Prompt Serialization**

LLM에 전달되는 최종 context는 다음 형식을 따른다.

```text
[IMAGE FINDINGS]
Fatty Liver — located in Liver — described by “increased echogenicity”.

[GRAPH PATHS]
1. Image → Finding → Anatomy
2. Finding → Related Finding

[CONTEXT META]
paths_len=3, path_strength=0.84, slot_rebalanced=True
```

---

## **6. LLM Runner (Reasoning Layer)**

### **6.1 역할**

- 입력 context를 기반으로 세 가지 모드(V, VL, VGL)를 병렬 수행
- reasoning 결과를 구조화된 JSON으로 반환

### **6.2 동작 모드**

| 모드    | 입력 구성             | 설명                     |
| ------- | --------------------- | ------------------------ |
| **V**   | Text-only             | LLM reasoning baseline   |
| **VL**  | Vision caption + text | VLM 결합 reasoning       |
| **VGL** | Graph evidence + text | Ontology-based reasoning |

각 모드의 결과는 동일한 finding slot 구조를 따른다.

---

### **6.3 Prompt 구조**

```text
SYSTEM: You are a radiology reasoning assistant.
INPUT: [IMAGE SUMMARY]
CONTEXT: [GRAPH FACTS] + [PATHS]
TASK: Summarize key findings and reasoning evidence.
```

prompt는 `prompt_builder.py`에서 자동 조립되며,
graph evidence가 존재하면 `anchor_mode = VGL`로 설정된다.

---

## **7. Consensus Core (합의 엔진)**

### **7.1 역할**

- V/VL/VGL 세 모드의 출력을 통합하여
  최종 신뢰도 높은 합의 결과(consensus)를 생성한다.

---

### **7.2 Weighted Ensemble**

```python
weights = {"V": 1.0, "VL": 1.2, "VGL": 1.8}
if has_paths:
    weights["VGL"] += 0.2
consensus = compute_consensus(
    results,
    weights=weights,
    anchor_mode="VGL" if has_paths else None,
    min_agree=0.35
)
```

- agreement_score ≥ 0.35 → “agree”
- < 0.35 → “degraded”
- conflict 시 “low confidence”로 분류

---

### **7.3 Consensus 결과 예시**

```json
{
  "status": "agree",
  "confidence": "high",
  "agreement_score": 0.71,
  "anchor_mode": "VGL",
  "notes": "graph evidence strong"
}
```

테스트: `tests/test_consensus_snapshot.py`

---

## **8. Debug Payload & Evaluation Interface**

### **8.1 Debug 구조**

`/pipeline/analyze?debug=1` 호출 시 다음 필드가 반환된다.

```json
{
  "stage": "context",
  "normalized_image": {...},
  "graph_context": {
    "paths_len": 3,
    "path_strength": 0.84,
    "slot_meta": {...}
  },
  "consensus": {...},
  "degraded": false
}
```

### **8.2 활용 목적**

- 실험 데이터 수집
- 성능 분석 (agreement_score, degraded_ratio)
- 논문용 결과 테이블 생성

---

## **9. 테스트 및 재현성 보장 (Reproducibility)**

| 테스트 종류      | 목적                           | 위치                               |
| ---------------- | ------------------------------ | ---------------------------------- |
| Unit Test        | Slot rebalance, consensus 검증 | `tests/unit/`                      |
| Integration Test | Graph migration, upsert 검증   | `tests/integration/`               |
| Snapshot Test    | 합의 결과 일관성 보장          | `tests/test_consensus_snapshot.py` |

테스트 결과는 GitHub Actions 환경에서 자동 실행 가능하며,
모든 주요 알고리즘은 deterministic seed 기반으로 동작한다.

---

## **10. 시스템 설계 요약**

| 계층         | 구성 요소                                                           | 역할              |
| ------------ | ------------------------------------------------------------------- | ----------------- |
| Presentation | FastAPI Router (`pipeline.py`)                                      | 요청 처리         |
| Service      | Normalizer / GraphRepo / ContextBuilder / LLMRunner / ConsensusCore | 핵심 로직         |
| Storage      | Neo4j / Seed Registry                                               | 데이터 저장       |
| Evaluation   | DebugPayload / Tests                                                | 실험 및 분석 지원 |

→ 이 설계는 **GraphRAG × Ontology × Vision Pipeline**의 통합 구조로,
논문 “System Design” 챕터의 완결된 형태를 이룬다.
