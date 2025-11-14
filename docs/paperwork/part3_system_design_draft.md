# Part III. SYSTEM DESIGN AND IMPLEMENTATION

## Chapter 11. 전체 시스템 아키텍처 개요

본 연구에서 제안하는 시스템은 세 개의 주요 계층 — **Vision**, **Graph**, **Language** — 으로 구성된다.  
각 계층은 서로 다른 데이터 표현 공간을 담당하며, Redis Stream 기반의 이벤트 파이프라인으로 연결된다.  
시스템의 전체 구조는 Figure 1과 같이 표현된다.

> **Figure 1. Vision–Graph–Language (VGL) Architecture Overview**  
> Image → Caption → Ontology Insertion → Graph Context Generation → LLM Inference → Evaluation

이 구조의 목적은 입력 이미지를 그래프 기반 의미 표현으로 정규화하고,  
이를 LLM의 컨텍스트로 제공하여 구조적 추론이 언어 생성에 미치는 영향을 검증하는 것이다.  
각 모듈은 독립적으로 동작하며, 모든 상호작용은 메시지 큐(Streams) 단위로 이루어진다.  
이 설계는 실험의 재현성과 확장성을 동시에 확보한다.

---

## Chapter 12. Vision Processing Layer

Vision Layer는 LLM 추론의 출발점으로서, 시각 입력을 언어로 변환한다.  
본 연구는 **BLIP-2** 기반 Vision–Language Model(VLM)을 사용하여 이미지를 캡션 텍스트로 변환하고,  
그 결과를 “normalized caption” 형태로 저장한다.  

캡션 전처리 과정은 다음 절차를 따른다:

1. **Caption Extraction** – 이미지 입력으로부터 1~3문장 요약 생성  
2. **Entity Normalization** – 의료 용어(예: “fatty liver”)를 표준 개념으로 정규화  
3. **Attribute Mapping** – modality, region, observation 속성 분리  
4. **JSON 구조화** – Neo4j 삽입을 위한 key–value 구조 변환  

이러한 정규화는 이미지 단위의 노이즈를 줄이고 Ontology 상 개체 노드와 일관된 연결을 가능하게 한다.  
예시 캡션은 다음과 같다.

```json
{
  "image_id": "IMG201",
  "modality": "Ultrasound",
  "finding": ["increased hepatic echogenicity"],
  "diagnosis": "fatty liver"
}
````

이 구조는 이후 Graph Layer에서 Ontology 노드 삽입의 기초 데이터로 사용된다.

---

## Chapter 13. Ontology 설계 및 Neo4j 스키마

Ontology Layer는 본 시스템의 핵심으로, 모든 추론 근거가 저장되는 구조적 공간이다.
Neo4j를 기반으로 설계된 Ontology는 Figure 2의 스키마를 따른다.

> **Figure 2. Ontology Schema (Node–Relation Model)**
> `Image` —HAS_FINDING→ `Finding` —RELATED_TO→ `Diagnosis` —MENTIONED_IN→ `Report`

각 노드는 고유 식별자(UUID)와 주요 속성을 가지며, 관계(Relationship)는 방향성과 속성값(weight, certainty)을 포함한다.
다음은 주요 노드 정의 요약이다.

| Node Type | 주요 속성                   | 설명            |
| :-------- | :---------------------- | :------------ |
| Image     | image_id, modality      | 입력 데이터의 고유 단위 |
| Finding   | finding_id, description | 관찰된 의료 소견     |
| Diagnosis | diag_id, label          | 질병 또는 상태 개념   |
| Report    | report_id, text         | 요약 리포트 문장     |

관계의 예시는 다음과 같다:

* `(Image)-[:HAS_FINDING]->(Finding)`
* `(Finding)-[:RELATED_TO]->(Diagnosis)`
* `(Diagnosis)-[:MENTIONED_IN]->(Report)`

이 구조는 그래프 상에서 최대 3-hop 깊이의 reasoning 경로를 구성하며,
Graph Context Generator가 LLM 입력용 컨텍스트를 생성할 때 활용된다.

---

## Chapter 14. Palantir Ontology와의 비교

Palantir Ontology는 대규모 데이터 통합을 위한 엔터프라이즈용 Ontology로,
복잡한 권한 제어·버전 관리·데이터 계보 추적 기능을 갖춘다.
그러나 연구 환경에서는 이러한 복잡성이 오히려 실험 통제를 어렵게 만든다.

| 구분  | Palantir Ontology | Lightweight Graph Ontology |
| :-- | :---------------- | :------------------------- |
| 목적  | 운영·정책·통합 중심       | 실험·검증 중심                   |
| 접근성 | 폐쇄형, API 의존       | 개방형, Cypher 직접 제어          |
| 스키마 | 다층 계보형            | 단일 레벨의 노드–관계 모델            |
| 추론  | Formal logic 기반   | 관계 패턴 기반 경로 탐색             |
| 장점  | 표준화, 데이터 무결성      | 단순성, 통제 가능성                |

본 연구의 Ontology는 “모델의 신뢰성 검증을 위한 구조적 컨텍스트”를 제공하기 위한 목적에서 설계되었으며,
Palantir 구조의 복잡한 governance 계층을 제거함으로써
LLM과의 통합 실험이 가능하도록 단순화되었다.

---

## Chapter 15. Graph Context Generator

Graph Context Generator는 Ontology에서 LLM으로 전달할 컨텍스트를 생성한다.
이 모듈은 **Cypher Query Template** 을 사용해 특정 이미지 노드로부터
k-hop(1~3) 내의 유효 경로를 추출한다.

예시 쿼리:

```cypher
MATCH (i:Image {image_id: $id})-[:HAS_FINDING]->(f:Finding)-[:RELATED_TO]->(d:Diagnosis)
RETURN i.image_id, f.description, d.label LIMIT 5;
```

추출된 경로는 자연어로 변환되어 LLM 입력 prompt에 삽입된다.
이 과정에서 중복 경로 제거(deduplication)와 다양성 점수(Path Diversity)를 계산한다.
Path diversity는 정보 다양성을 나타내며, Consistency와 trade-off 관계를 가진다.

---

## Chapter 16. Redis Stream 기반 비동기 파이프라인

Vision–Graph–Language 모듈은 서로 독립된 프로세스로 동작한다.
모듈 간 데이터를 안전하게 교환하기 위해 Redis Stream을 사용하여
**Event-driven architecture** 를 구성하였다.

### 주요 구성

* **Stream key:** `vision_pipeline_events`
* **Consumer group:** `vgl_group`
* **Event types:** `caption_ready`, `graph_updated`, `inference_complete`
* **Timeout:** 500 ms 블록 대기
* **Retention:** maxlen=1000 (approximate)

이 구조는 각 모듈의 비동기 처리를 허용하고,
이벤트 로그를 통해 전 과정의 상태를 추적 가능하게 만든다.
또한 동일 파이프라인이 로컬 및 클라우드 환경에서 동일하게 재현될 수 있다.

---

## Chapter 17. LLM Inference Layer

Language Layer는 최종적으로 LLM에게 Vision과 Graph 정보를 제공하고 추론 결과를 생성한다.
본 연구는 GPT 계열 모델을 사용하여 세 가지 모드로 실험을 수행하였다.

| Mode | 입력 구성                           | 설명                    |
| :--- | :------------------------------ | :-------------------- |
| V    | Image caption only              | 시각정보 기반 baseline      |
| VL   | Image + normalized caption      | 언어 grounding 추가       |
| VGL  | Image + caption + graph context | Ontology grounding 추가 |

Prompt 예시:

```
You are a medical assistant.  
Given the following image description and findings, write a concise diagnostic summary.

Image caption: "Ultrasound image showing increased hepatic echogenicity."  
Ontology context:  
- Finding: fatty liver  
- Diagnosis: hepatic steatosis  
- Related reports: 2
```

VGL 모드는 Ontology context가 포함된 상태에서 추론하며,
각 실험은 동일 temperature=0.2, seed 고정 조건으로 수행되었다.

---

## Chapter 18. Evaluation Engine

실험의 자동화를 위해 평가 엔진을 별도로 구성하였다.
이 엔진은 각 실험 실행 후 모델 출력을 수집하고
Hallucination Rate(HR), Consistency Score(CS), Path Diversity(PD)를 계산한다.

### 평가 지표 계산 절차

1. **Hallucination Rate (HR):**
   출력 문장 내 존재하지 않는 개체의 비율
   [
   HR = \frac{N_{false}}{N_{facts}}
   ]
2. **Consistency Score (CS):**
   동일 이미지에 대한 n회 응답 간 의미 유사도 (BERTScore 기반)
3. **Path Diversity (PD):**
   그래프 경로 분포의 엔트로피 값으로 정보 다양성 측정

모든 결과는 Redis Stream 이벤트 로그와 함께 수집되어
Neo4j 기반 메타데이터 레지스트리에 저장된다.

---

## Chapter 19. 시각화 및 로깅

Neo4j Browser 및 custom Python 시각화 모듈을 이용해
각 실험의 컨텍스트 경로와 추론 결과를 시각적으로 기록한다.

* **ctx_paths_len:** Graph context 깊이
* **agreement_score:** VL vs VGL 응답 일치율
* **timeline logs:** Vision → Graph → LLM → Eval 전체 흐름
* **dashboard:** Stream consumer 상태, latency, success rate 시각화

이 로깅 체계는 시스템 디버깅과 실험 분석 모두에 활용된다.

---

## Chapter 20. 실험 환경 및 배포 구조

실험은 Ubuntu 22.04 기반 Docker Compose 환경에서 수행되었다.
각 모듈은 컨테이너 단위로 분리되어 있으며,
FastAPI(backend), Redis, Neo4j, evaluation worker로 구성된다.

| 구성요소          | 기술 스택                 | 역할                   |
| :------------ | :-------------------- | :------------------- |
| Backend       | FastAPI + Python 3.11 | REST + orchestration |
| GraphDB       | Neo4j 5.14            | Ontology 저장          |
| Cache/Queue   | Redis 7.2 Stream      | 이벤트 관리               |
| LLM Interface | OpenAI / Bedrock API  | 추론 수행                |
| Evaluation    | Python script         | 지표 계산 및 로그 집계        |

환경 변수는 `.env` 대신 AWS Secrets Manager를 통해 관리하여 재현성과 보안을 확보하였다.
모든 실험은 동일 버전의 컨테이너 이미지에서 수행되어 결과의 일관성을 보장한다.

---

### 요약

Part III는 제안한 VGL 시스템의 구체적 설계와 구현 과정을 다루었다.
이 구조는 LLM이 Ontology 기반 구조적 근거를 참조할 수 있는 실험 환경을 제공하며,
Part IV에서는 이 시스템을 활용해 수행한 실험 설계, 데이터셋 구성, 평가 지표 정의를 상세히 설명한다.

