# 📘 **Part I — 서론 (Introduction)**

_Ontology-Grounded Vision-Language Reasoning Pipeline for Medical Imaging_
_초안 / Draft v1.0_

---

# **1. 연구 배경**

현대 의료 영상 분석은 크게 두 가지 흐름으로 발전하고 있다.
첫째, Vision–Language Model(VLM)을 활용한 직접적 추론(direct reasoning),
둘째, 외부 지식(knowledge)을 결합한 증강 추론(augmented reasoning)이다.

VLM은 단일 영상에서 상당히 자연스러운 캡션을 생성할 수 있으며,
최근 GPT-4o 계열, Gemini 1.5 Pro 계열의 등장으로 두 modality를 동시에 처리하는 성능이 크게 향상되었다.
그러나 이러한 모델은 **의학적 정확성(medical accuracy)** 이 요구되는 환경에서는 두 가지 한계를 보인다:

1. **Hallucination**

   - 영상과 직접적 관련이 없는 병변을 언급하거나
   - modality에 맞지 않는 표현(예: CT 영상에서 “Doppler artefact”)이 나타남.

2. **일관성 부족(inconsistency)**

   - 동일한 영상에 대해 프롬프트를 바꿀 때마다 상이한 진단적 서술을 생성.

3. **근거 부족(lack of evidence)**

   - “왜 그런 판단을 했는가?”에 대한 중간 근거가 부재하여,
     의사가 신뢰하기 어려움.

이러한 한계를 보완하기 위해 등장한 것이 **RAG (Retrieval-Augmented Generation)**,
그리고 그 확장판인 **GraphRAG(그래프 기반 RAG)**이다.
GraphRAG는 외부 지식의 구조를 그래프로 표현하여,
VLM 또는 LLM이 동작할 때 **연관 개념, 병변, 위치 정보를 경로(Path Evidence)로 제공**함으로써
추론의 신뢰도를 높이는 방법이다.

하지만 일반 RAG/GraphRAG는 텍스트 중심이며,
**의료 영상**이라는 multimodal 입력을 다루기 위한 전용 파이프라인을 제공하지 않는다.
즉,

- 영상 → 텍스트 변환
- 텍스트 → 그래프 상의 관련 노드 탐색
- 그래프 맥락 → LLM reasoning

의 과정이 자연스럽게 연결된 구조가 거의 없다.

본 연구는 이 간극을 해소하여,
**의료 영상 기반 GraphRAG 파이프라인**을 실험적으로 구축하고,
VLM과 LLM 간의 reasoning 일관성을 분석하는 것을 목표로 한다.

---

# **2. 연구 목적**

본 연구에서는 다음 세 가지 핵심 목적을 가진다.

---

### **(1) Ontology-Grounded Vision Pipeline의 구축**

영상 입력을 **Graph Ontology**로 연결하는 end-to-end 파이프라인을 구현한다.

- Vision Normalization
- Graph Upsert
- Graph Context Packing
- LLM Reasoning (V / VL / VGL 모드)
- Consensus Engine

이 전체 흐름은 실제 시스템(`/pipeline/analyze`) 하나로 완결된다.

---

### **(2) 그래프 기반 근거(Graph Evidence)의 효과 검증**

그래프가 제공하는 다음 정보가 실제 reasoning에 어떤 영향을 미치는지 실험한다.

- multi-hop path evidence
- finding–anatomy 관계
- triple 기반 지식 표현
- slot rebalancing에 따른 context depth 증가

이를 통해 GraphRAG가 의료 영상 reasoning에서
**hallucination 감소**, **일관성 증가**, **신뢰도 향상**에 기여하는지 검증한다.

---

### **(3) V / VL / VGL 모드 비교 실험**

세 가지 모드를 실험적으로 비교한다.

| 모드    | 설명                          |
| ------- | ----------------------------- |
| **V**   | 텍스트 기반 LLM reasoning     |
| **VL**  | VLM이 생성한 캡션 포함        |
| **VGL** | Graph Evidence 기반 reasoning |

이 비교를 통해:

- graph evidence가 reasoning의 어떤 부분을 개선하는지
- VLM caption이 오히려 noise로 작용하는 경우는 무엇인지
- consensus 모델이 어떻게 안정된 결론을 도출하는지

를 분석한다.

---

# **3. 연구 기여 (Contributions)**

본 연구의 기여는 다음 네 가지로 요약된다.

---

## **(1) Ontology-Aware Vision-Language Pipeline 제안**

영상 입력에서 그래프 기반 추론까지 이어지는
**완전한 파이프라인을 구현**하였다.

본 시스템의 독창적인 부분은:

- slot 기반 context pack
- fallback path synthesis
- graph-weighted evidence
- multi-hop path segment scoring

이며 이는 기존 RAG/GraphRAG 구현과 차별되는 구성이다.

---

## **(2) Graph Evidence 기반 LLM Consensus Engine**

본 연구는 단일 모델의 출력을 그대로 사용하는 대신,
세 모드(V, VL, VGL)를 ensemble하여 **consensus output**을 생성한다.

특히:

- **VGL anchor mode**
- weight-tuning (VL 1.2, VGL 1.8 + bonus)
- agreement score
- degraded classification

같은 시스템은 의료 영상 reasoning 실험에 특화된 기능이다.

---

## **(3) Deterministic Graph Upsert + Seed Registry 구조**

연구 reproducibility를 위해 다음을 엄격하게 통제하였다.

- 고정된 Image ID Policy
- Seeded Finding Registry
- Fallback metadata logging
- deterministic upsert query

이를 통해 실험 환경이 매 실행마다 동일하게 유지되며,
논문 실험의 신뢰성을 보장한다.

---

## **(4) 실험 기반 분석 및 에러 구조 정량화**

본 연구는 단순히 파이프라인을 만들 뿐만 아니라,
다음과 같은 정량적 분석 지표를 설계하였다:

- agreement score
- path strength
- degraded ratio
- consensus confidence

이를 활용해 **V/VL/VGL 간 reasoning 차이**를 실험적으로 검증한다.

---

# **4. 논문의 구성**

본 논문의 전체 구성은 다음과 같다.

1. **Part I — 서론**: 문제 정의, 연구 목적, 기여
2. **Part II — 이론적 배경**: VLM, Ontology, GraphRAG, Consensus
3. **Part III — 시스템 설계**: Vision Normalizer, Graph Upsert, Context Builder, Consensus
4. **Part IV — 실험 설계**: 모드 비교, path ablation, 지표 정의
5. **Part V — 결과**: 실험 결과 및 지표 분석
6. **Part VI — 결론 및 논의**: 한계와 향후 연구 방향
7. **부록**: graph schema, cypher, debugging 사례 등
