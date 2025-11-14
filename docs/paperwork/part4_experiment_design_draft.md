# Part IV. EXPERIMENT DESIGN AND DATASETS

## Chapter 21. 실험 목적 (Experimental Objectives)

본 실험의 목적은 Ontology grounding이 LLM의 추론 결과에 미치는 영향을 정량적으로 규명하는 것이다.  
특히, Vision–Language–Graph 통합 구조(VGL)가 Hallucination 감소 및 Consistency 향상에  
실질적인 효과를 가지는지를 검증한다.

이 실험은 다음 세 가지 핵심 목표를 가진다.

1. **Grounding 효과 분석** – Ontology 컨텍스트 유무에 따른 LLM 응답 품질 차이 평가  
2. **구조 복잡도 영향 분석** – Graph depth와 path 다양성이 추론 품질에 미치는 영향 검증  
3. **시스템 비교 분석** – Palantir-style Ontology 모사형 vs Lightweight Graph Ontology의 성능 차이 평가

모든 실험은 동일한 입력 이미지·질의 세트를 기반으로 수행되며,  
모델의 내부 파라미터(temperature, seed)는 고정하여 오직 구조적 변수만을 조작한다.

---

## Chapter 22. 데이터셋 구조 (Dataset Composition)

본 연구에 사용된 데이터셋은 비식별화된 의료 dummy dataset으로,  
각 항목은 Image–Finding–Diagnosis–Report의 구조로 구성된다.  
이는 실제 임상 데이터의 Ontology 구조를 모사하되, 개인 식별 정보를 완전히 제거하였다.

| 데이터 구성요소 | 개수 | 예시 |
|:--|:--|:--|
| Images | 300 | IMG001–IMG300 |
| Findings | 650 | “increased hepatic echogenicity”, “calcified nodule” |
| Diagnoses | 120 | “fatty liver”, “hepatitis”, “gallstone” |
| Reports | 300 | 1~3문장 요약 형태 |
| Relationships | 1200+ | HAS_FINDING, RELATED_TO, MENTIONED_IN 등 |

데이터는 Neo4j로 시드(seed) 스크립트를 통해 삽입되며,  
각 노드는 다음과 같은 속성 구조를 가진다.

```cypher
CREATE (i:Image {id:"IMG201", modality:"CT"}) 
CREATE (f:Finding {desc:"fatty infiltration"}) 
CREATE (d:Diagnosis {label:"fatty liver"}) 
CREATE (i)-[:HAS_FINDING]->(f)-[:RELATED_TO]->(d);
````

이 구조를 통해 그래프 내 reasoning path를 1~3-hop 범위에서 통제할 수 있다.

---

## Chapter 23. 실험 변수 정의 (Experimental Variables)

본 실험의 주요 변수는 **Ontology context 존재 여부**, **Graph depth**, **Path diversity** 이다.

| 구분     | 변수명                     | 설명                    | 유형  |
| :----- | :---------------------- | :-------------------- | :-- |
| 독립변수 1 | Context mode            | V / VL / VGL          | 범주형 |
| 독립변수 2 | Graph depth             | 1-hop / 2-hop / 3-hop | 순서형 |
| 독립변수 3 | Path diversity          | 고/중/저 다양성 샘플          | 순서형 |
| 종속변수 1 | Hallucination Rate (HR) | 사실 오류율                | 연속형 |
| 종속변수 2 | Consistency Score (CS)  | 응답 일관성                | 연속형 |
| 종속변수 3 | Path Diversity (PD)     | 경로 다양성 엔트로피           | 연속형 |

모든 변수는 독립적으로 조합되어 9개 실험 조건(VGL × depth × diversity)을 구성한다.

---

## Chapter 24. 평가 지표 정의 (Evaluation Metrics)

세 가지 주요 지표를 정의한다.

### (1) Hallucination Rate (HR)

모델 출력 중 그래프 근거에 존재하지 않는 개체의 비율로 정의한다.
[
HR = \frac{N_{\text{false}}}{N_{\text{facts}}}
]
낮을수록 사실적 정확성이 높음을 의미한다.

### (2) Consistency Score (CS)

동일 이미지에 대해 반복 질의한 응답 간 의미 유사도 (BERTScore 또는 cosine similarity 기반).
[
CS = \text{mean}( \text{Sim}(y_i, y_j) ), \quad i,j \in {1,2,...,n}
]
높을수록 추론 일관성이 높다.

### (3) Path Diversity (PD)

Ontology에서 추출된 경로의 정보 다양성을 Shannon entropy로 측정한다.
[
PD = - \sum_{k} p_k \log p_k
]
이는 추론 경로의 다양성과 지식 확장의 균형을 평가한다.

---

## Chapter 25. Palantir vs Graph Ontology 비교 설계

Palantir Ontology 구조를 실험적으로 모사하기 위해
노드 스키마와 관계 구조를 계층형(hierarchical)으로 변환한 대조군을 구축하였다.
두 구조의 차이를 다음 표에 요약한다.

| 항목      | Palantir 모사형                      | Lightweight Graph Ontology |
| :------ | :-------------------------------- | :------------------------- |
| 계층 구조   | 4-tier (Entity–Concept–Fact–Edge) | 2-tier (Node–Relation)     |
| 추론 방식   | Formal inheritance                | Path traversal             |
| 쿼리 복잡도  | O(n²)                             | O(n)                       |
| 컨텍스트 길이 | 평균 1024 tokens                    | 평균 420 tokens              |
| 실행 시간   | 평균 3.8s                           | 평균 1.5s                    |

두 시스템 모두 동일 질의 세트를 입력으로 사용하며,
각 질의는 동일한 Graph depth(2-hop)와 Path diversity 조건에서 수행된다.
비교 지표는 HR, CS, latency, reproducibility 네 가지다.

---

## Chapter 26. Query Set 구성

실험 질의 세트는 의료 영상 해석에 일반적으로 등장하는 패턴을 기반으로 설계하였다.
총 100개 질의가 사용되며, 유형별 분포는 다음과 같다.

| 질의 유형                 | 개수 | 예시                                                  |
| :-------------------- | :- | :-------------------------------------------------- |
| Diagnosis reasoning   | 40 | “What is the most likely diagnosis for this image?” |
| Finding extraction    | 30 | “List the main findings visible in this image.”     |
| Report summarization  | 20 | “Summarize the key abnormalities.”                  |
| Cross-check reasoning | 10 | “Are there contradictory findings?”                 |

각 질의는 동일 이미지 세트(IMG001–IMG300)에 대해 무작위 샘플링되어 수행된다.
결과는 LLM temperature=0.2, repetition=3회로 수집된다.

---

## Chapter 27. Ablation Study 설계

Ablation study는 Ontology 구조와 retrieval 설정의 변형이 결과에 미치는 영향을 분석하기 위해 수행된다.

| 실험명            | 조작 변수                  | 목적                  |
| :------------- | :--------------------- | :------------------ |
| Depth-Ablation | Graph depth: 1,2,3-hop | reasoning 깊이의 한계 분석 |
| Path-Ablation  | Path ranking on/off    | 중요도 기반 경로 선택 영향     |
| SNOMED-Mapping | 표준 용어 매핑 on/off        | Ontology 정규화 효과 검증  |

각 실험은 동일한 질의 세트를 기준으로 수행되며,
변경된 설정이 HR·CS·PD 지표에 미치는 평균 변화량(Δ)을 분석한다.

---

## Chapter 28. 자동화 및 재현성 (Reproducibility)

모든 실험은 GitHub Actions를 통해 자동화되며,
다음 단계별 workflow로 구성된다.

1. **Seed Graph Load** – Neo4j에 dataset 삽입
2. **Pipeline Execution** – Vision → Graph → LLM inference 수행
3. **Evaluation Run** – metric 계산 및 로그 수집
4. **Result Export** – JSON + CSV 파일 저장

결과는 `results/{date}/metrics.json` 으로 기록되며,
모든 환경은 동일 Docker 이미지에서 수행되어 재현성이 보장된다.

---

## Chapter 29. 환경 변수 통제 (Controlled Conditions)

* LLM: GPT-4-turbo, temperature=0.2
* Random seed: 42 고정
* Context length: 4096 tokens 제한
* Redis Stream retention: 1000 events
* Ontology schema version: v1.2 (고정)
* Batch size: 10 질의 단위

이 통제를 통해 모델 내부의 불확실성을 최소화하고,
Ontology 구조만을 독립 변수로 평가할 수 있다.

---

## Chapter 30. 윤리적 고려 (Ethical Considerations)

모든 데이터는 dummy dataset으로 생성되었으며,
실제 환자 정보·식별자·의료 기록은 포함되지 않는다.
이미지와 텍스트는 공개된 오픈소스 의료 데이터의 통계적 패턴을 기반으로 합성되었다.

* **비식별성:** UUID 기반 image_id 부여
* **데이터 보안:** 외부 네트워크 접근 불가 (로컬 Neo4j)
* **출력 제한:** LLM 응답 중 개인 식별 정보 자동 필터링
* **공개 정책:** 모델 및 데이터셋 구조는 오픈소스로 제공하되, dummy 원본 데이터는 비공개 유지

---

### 요약

Part IV는 실험의 변수, 데이터, 평가 지표, 통제 조건을 정의하였다.
Ontology grounding 효과를 검증하기 위한 실험 설계가 완성되었으며,
이제 Part V에서 실제 결과 및 정량적 분석을 통해
각 가설(H1–H3)을 검증한다.
