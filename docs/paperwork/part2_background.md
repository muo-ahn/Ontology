# 📘 **Part II — 이론적 배경 (Background)**

_Ontology-Grounded Vision-Language Reasoning Pipeline for Medical Imaging_
_Draft v1.0 (Updated to main branch implementation)_

---

## **1. Vision–Language Model (VLM)의 개념과 한계**

Vision–Language Model(VLM)은 시각적 입력을 언어적 표현으로 변환하여
텍스트 기반 reasoning에 활용하는 멀티모달 모델이다.
대표적으로 CLIP, BLIP, LLaVA, GPT-4o 계열이 존재한다.

이들은 “이미지 → 텍스트” 변환을 통해 모델이 이해 가능한 입력으로 바꾸지만,
의료 도메인에서는 세 가지 주요 한계가 발견된다.

---

### **(1) 비의료적 캡션(hallucination)**

VLM은 자연 이미지에서 학습된 분포를 기반으로 캡션을 생성하므로,
의료 영상(CT, MRI, Ultrasound 등)에서는 비정상적 표현이 자주 발생한다.
예를 들어, 초음파 영상에서 “beautiful landscape”와 같은 non-clinical 표현을 반환하는 경우도 존재한다.

→ 코드 상에서는 이 문제를 **mock-caption fallback**으로 감지하고,
`finding_fallback.source = "vlm_mock_caption"` 으로 표시된다.
즉, VLM이 임상적 텍스트를 생성하지 못했을 때의 보호장치다.

---

### **(2) 추론 근거의 부재**

기존 VLM은 단순히 캡션만 생성할 뿐,
“왜 그렇게 판단했는가?”에 대한 **근거(evidence)**를 제공하지 않는다.
이로 인해 설명가능성(Explainability)이 떨어지고,
결과의 신뢰성(reliability) 평가가 어렵다.

→ 본 연구에서는 이를 **Graph Ontology 기반 Evidence Path**로 보완한다.
즉, 이미지에 대응하는 그래프 경로를 생성하여,
LLM이 reasoning 시 해당 근거를 prompt context로 받는다.

---

### **(3) 일관성(Consistency) 문제**

동일 이미지에 대해 서로 다른 문장 입력을 주면
서로 다른 진단적 결론을 내는 경향이 있다.
이는 “비구조적 캡션 기반 reasoning”의 구조적 한계다.

→ 본 연구에서는 LLM layer에 **V/VL/VGL Consensus Core**를 두어,
모드 간 일관성을 정량화(agreement_score)하고
불일치(conflict) 상태를 degraded case로 분류한다.

---

## **2. Ontology와 의료 지식 그래프 (Knowledge Graph)**

---

### **(1) Ontology 개요**

Ontology는 도메인 지식을 구조적으로 표현하는 방법으로,
개체(entity)와 관계(relation)의 집합으로 정의된다.
의료 영상 도메인에서는 일반적으로 다음과 같은 개념 계층이 존재한다:

| 계층    | 예시 노드                | 예시 관계              |
| ------- | ------------------------ | ---------------------- |
| Imaging | CT, Ultrasound, X-ray    | has_modality           |
| Finding | Fatty Liver, Nodule      | located_in, related_to |
| Anatomy | Liver, Lung              | contains               |
| Report  | “increased echogenicity” | described_by           |

이 구조는 Neo4j 그래프 DB에서 **노드(Label)**와 **간선(Relation)**으로 표현된다.

---

### **(2) 본 연구에서의 Ontology 구성**

현재 시스템에서 정의된 스키마(Neo4j Constraints 기준)는 다음과 같다.

- `(:Image {image_id})`
- `(:Finding {label, score})`
- `(:Anatomy {label})`
- `(:Report {text})`
- 관계(Relations):

  - `HAS_FINDING`
  - `DESCRIBED_BY`
  - `LOCATED_IN`
  - `RELATED_TO`
  - `SIMILAR_TO`

이는 `/schema/v1_1/constraints.cypher` 및
`services/graph_repo.py`의 `UPSERT_CASE_QUERY`에서 구현되어 있다.

---

### **(3) Graph Ontology의 역할**

Ontology는 단순 데이터 저장소가 아니라,
다음과 같은 **추론 기반 맥락(Context Reasoning)**의 중심축이다.

1. 이미지–병변–해부학 구조를 통해 **다단계 연관(Path Evidence)**을 찾는다.
2. Path Evidence는 LLM 프롬프트에 삽입되어 **의학적 근거 제시** 역할을 한다.
3. 경로의 길이, 가중치(path_score, path_strength)가 reasoning confidence에 반영된다.

이 구조는 코드 상 `GraphContextBuilder` 클래스에서
`GRAPH_PATHS_QUERY` 및 `_rebalance_slot_limits`, `_ensure_finding_slot_floor` 로 구현되어 있다.

---

## **3. GraphRAG (Graph-based Retrieval Augmented Generation)**

---

### **(1) 기존 RAG의 개념**

RAG는 LLM이 외부 지식을 활용하도록
검색(retrieval)된 문서를 입력 context에 추가하는 방식이다.
하지만 일반 RAG는 **비구조적 텍스트**만을 다루기 때문에,
정보 간 관계를 표현하거나 근거를 시각화하기 어렵다.

---

### **(2) GraphRAG의 등장**

GraphRAG은 RAG의 retrieval 단계를 그래프 탐색으로 대체한다.
이는 특히 의료 분야처럼 관계(relationship)가 중요한 도메인에서
문맥적 일관성과 추론 깊이를 확보하는 데 효과적이다.

본 연구의 파이프라인은 GraphRAG의 구조를 그대로 계승하되,
**입력 modality를 “영상 기반”으로 확장**한 점이 차별점이다.

---

### **(3) 본 시스템의 GraphRAG 구현 구조**

| 단계                    | 설명                          | 실제 코드                             |
| ----------------------- | ----------------------------- | ------------------------------------- |
| 1. Graph Retrieval      | 이미지 ID 기준 Path 탐색      | `GraphRepo.query_paths()`             |
| 2. Path Scoring         | path_strength 계산            | `context_pack._score_path_segments()` |
| 3. Context Packing      | slot별 evidence 패킹          | `GraphContextBuilder`                 |
| 4. Fallback Synthesis   | 경로 없음 시 가상 triple 생성 | `_build_fallback_path_rows()`         |
| 5. Prompt Serialization | LLM 프롬프트용 텍스트 변환    | `_format_paths_for_prompt()`          |

이 전 과정이 실제 `/pipeline/analyze` 호출 내부에서 자동으로 수행된다.

---

## **4. Consensus 및 앙상블 이론**

---

### **(1) 복수 모드 추론의 필요성**

의료 영상에서의 추론은 다양한 시각적 특징과 텍스트적 근거에 따라
결과가 달라질 수 있다.
따라서 단일 모드(VLM 또는 텍스트 기반 LLM)보다
복수 모드의 출력을 통합하는 **앙상블(Ensemble)** 접근이 유리하다.

---

### **(2) Consensus Model의 기본 원리**

Consensus는 다수의 예측 결과 중
가장 일치하는 출력을 선택하거나,
weighted average로 대표 출력을 구성하는 기법이다.

본 연구에서는 세 모드(V, VL, VGL)의 출력을 비교하고,
아래 수식에 따라 **agreement_score**를 계산한다.

[
\text{agreement_score} = \frac{|S_V \cap S_{VL} \cap S_{VGL}|}{|S_V \cup S_{VL} \cup S_{VGL}|}
]

- (S_x) : 각 모드가 생성한 finding slot 집합
- score ≥ 0.35 → agree
- score < 0.35 → degraded

→ 실제 코드: `consensus_core.compute_consensus()`.

---

### **(3) Weight 기반 합의 (Weighted Consensus)**

각 모드의 신뢰도를 동적으로 조정하기 위해
가중치(weight)를 설정하였다.

[
w_V = 1.0,\quad
w_{VL} = 1.2,\quad
w_{VGL} = 1.8 + \delta
]

- (\delta = 0.2) (path evidence가 있을 경우 bonus)
- anchor mode = VGL

이 방식은 의료 영상 reasoning에서
“그래프 근거가 존재하는 출력을 anchor로 삼아”
다른 모드를 조정하는 구조이다.

→ 실제 코드:
`pipeline.analyze()` 내부 `weights` 계산 및 `anchor_mode` 지정 부분.

---

### **(4) Consensus 결과 구조**

`consensus` 객체는 다음 필드를 포함한다.

| 필드              | 설명                            |
| ----------------- | ------------------------------- |
| `status`          | agree / conflict / degraded     |
| `confidence`      | high / medium / low             |
| `anchor_mode`     | 기준 모드                       |
| `agreement_score` | 일치율                          |
| `notes`           | path evidence, degraded 사유 등 |

이 구조는 `/tests/test_consensus_snapshot.py`에서 검증된다.

---

## **5. 의료 AI에서의 설명가능성 (Explainable AI)와 본 연구의 위치**

기존 의료 AI 연구는 주로 모델 성능 향상에 집중해 왔으나,
의사 입장에서는 “왜 이런 판단을 내렸는가?”가 훨씬 중요하다.
본 연구는 모델의 내부 신뢰도를 외부 구조(graph)로 드러내어
**설명가능성(Explainability)**을 강화하는 것을 목표로 한다.

---

| 구분        | 기존 VLM 기반 접근 | 본 연구의 Ontology 기반 접근        |
| ----------- | ------------------ | ----------------------------------- |
| 입력        | 영상(CT, US 등)    | 동일                                |
| 내부 구조   | 캡션 → LLM         | 캡션 + Graph Evidence → LLM         |
| 근거 표현   | 단순 텍스트        | triple, path, node-level reasoning  |
| 출력 신뢰도 | 불명확             | confidence, degraded_ratio로 정량화 |
| 검증 단위   | 단일 샘플          | Path-level Consensus 단위           |

---

이로써 본 연구의 Ontology 기반 파이프라인은
**GraphRAG + Consensus + Vision pipeline의 융합형 구조**로서
기존 모델 대비 explainability를 실질적으로 강화한다.
