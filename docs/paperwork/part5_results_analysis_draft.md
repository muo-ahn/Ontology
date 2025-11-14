# Part V. EXPERIMENT RESULTS AND ANALYSIS

---

## Chapter 31. 전체 결과 개요 (Overview of Results)

Ontology grounding은 LLM의 추론 품질에 유의미한 변화를 일으켰다.  
세 가지 주요 지표(HR, CS, PD)에 대한 평균값은 Table 1과 같다.

| Mode | HR ↓ | CS ↑ | PD ↑ | 비고 |
|:--|:--:|:--:|:--:|:--|
| V | 0.312 | 0.672 | 0.41 | Vision only baseline |
| VL | 0.245 | 0.733 | 0.48 | Language caption 추가 |
| VGL | **0.176** | **0.812** | 0.53 | Ontology grounding 적용 |

Hallucination Rate는 43.6% 감소했으며 (V→VGL),  
Consistency Score는 20.8% 향상되었다.  
또한 Path Diversity 역시 소폭 증가하여, Ontology context가  
모델의 탐색적 추론 폭을 확장시켰음을 시사한다.

통계적으로, VGL 모드의 HR 감소는 p < 0.01 수준에서 유의미하며,  
CS 상승은 p < 0.05 수준에서 검증되었다.

---

## Chapter 32. Hallucination 감소 효과 (Reduction of Hallucination)

Figure 3은 세 모드(V, VL, VGL)의 Hallucination Rate 변화를 나타낸다.  

> **Figure 3. HR by Context Mode**  
> ![HR Graph Placeholder]

Ontology grounding은 모델이 근거 없는 개체를 생성하는 비율을 명확히 줄였다.  
예를 들어 baseline(V) 모드에서는 “focal lesion”이나 “gallbladder wall thickening” 등  
Ontology 내 존재하지 않는 소견이 31% 빈도로 등장했으나,  
VGL 모드에서는 8% 수준으로 감소하였다.  

이 결과는 **Ontology가 LLM의 “semantic search space”를 제한**함으로써  
Hallucination을 구조적으로 억제함을 보여준다.  
이는 H1 (“Ontology grounding은 Hallucination을 감소시킨다.”)을 지지한다.

---

## Chapter 33. Consistency 향상 (Improvement of Consistency)

동일 이미지에 대해 세 번 반복 질의한 응답 간 의미 유사도(BERTScore) 결과는 다음과 같다.

| Mode | CS 평균 | 표준편차 | Δ 대비(V) |
|:--|:--:|:--:|:--:|
| V | 0.672 | 0.094 | — |
| VL | 0.733 | 0.082 | +9.1% |
| VGL | **0.812** | 0.064 | **+20.8%** |

Ontology context를 제공한 경우,  
응답 간 단어 선택은 다양하지만 의미적 핵심이 일관되게 유지되었다.  
예를 들어 baseline(V)에서는 동일 이미지에 대해  
“fatty liver” ↔ “liver inflammation” ↔ “hepatic lesion” 등으로 표현이 변했으나,  
VGL 모드에서는 모든 응답이 “hepatic steatosis”로 수렴하였다.

이는 Ontology가 **정의된 개체 집합을 기준(anchor)** 으로  
LLM의 표현 다양성을 안정화시킨 결과로 해석된다.  
H2 (“Ontology grounding은 Consistency를 향상시킨다.”)는 실증적으로 지지되었다.

---

## Chapter 34. Path 다양성 분석 (Path Diversity Analysis)

Graph depth와 관계 패턴 수에 따라 Path Diversity(PD)가 변화하였다.

| Graph depth | PD (평균 엔트로피) | CS | HR |
|:--|:--:|:--:|:--:|
| 1-hop | 0.32 | 0.78 | 0.21 |
| 2-hop | **0.53** | **0.81** | **0.18** |
| 3-hop | 0.49 | 0.79 | 0.19 |

PD는 2-hop에서 가장 높았으며, 이는 Ontology가 충분히 풍부하면서도  
잡음이 과도하지 않은 최적 구조임을 시사한다.  
3-hop 이상에서는 비관련 노드가 혼입되어 CS가 약간 감소하였다.  
결과적으로 **정보 다양성과 응답 일관성 간에는 완만한 trade-off** 가 존재한다.

---

## Chapter 35. Prompt 형식의 영향 (Effect of Prompt Formatting)

Prompt에 Ontology 정보를 포함하는 방식이 결과에 미치는 영향을 분석하였다.  
JSON 형태 vs 자연어 문장 형태 두 가지 형식을 비교한 결과는 Table 2와 같다.

| 입력 형식 | HR ↓ | CS ↑ | 평균 latency |
|:--|:--:|:--:|:--:|
| JSON facts | 0.182 | 0.801 | 2.4s |
| Natural text | **0.176** | **0.812** | **2.1s** |

자연어 형식이 소폭 더 나은 결과를 보였다.  
이는 LLM이 구조화된 텍스트보다 자연 언어 기반 context를 더 유연하게 처리하기 때문으로 추정된다.  
Prompt 내 Ontology 정보는 “structured guidance” 역할을 수행하되,  
언어 모델의 확률적 생성 과정과 호환되어야 최적의 효과를 낸다.

---

## Chapter 36. Depth Ablation 결과

Graph depth(1–3-hop)에 따른 성능 변화는 Figure 4에 요약된다.

> **Figure 4. Depth-Ablation Results (HR, CS vs Hop)**  
> ![Depth Graph Placeholder]

2-hop에서 HR 최소(0.18), CS 최대(0.81)로 관찰되었다.  
이는 Ontology 경로가 너무 짧으면 정보가 부족하고,  
너무 깊으면 비관련 노이즈가 증가한다는 점을 보여준다.  
따라서 실험적으로 “Ontology reasoning depth의 최적 수준은 2-hop”임이 확인되었다.

---

## Chapter 37. SNOMED Mapping 실험

SNOMED-CT 기반 표준 용어 매핑을 적용한 결과,  
Hallucination Rate가 추가로 약 9% 감소하였다.  
이는 Ontology 내부의 중복·동의어 문제가 정규화되면서  
LLM의 혼동이 줄어든 결과로 해석된다.

| 조건 | HR | CS |
|:--|:--:|:--:|
| Mapping off | 0.176 | 0.812 |
| Mapping on | **0.160** | **0.825** |

이는 향후 Ontology의 표준화와 확장이 성능 향상에 직접 기여할 수 있음을 시사한다.

---

## Chapter 38. Palantir 모사형 대비 비교 결과

Palantir-style Ontology 모사형과 Lightweight Graph Ontology 간의 성능을 비교하였다.  

| 구조 | HR ↓ | CS ↑ | 실행 시간(s) | 재현성(%) |
|:--|:--:|:--:|:--:|:--:|
| Palantir 모사형 | 0.219 | 0.773 | 3.8 | 91.2 |
| Lightweight Graph Ontology | **0.176** | **0.812** | **1.5** | **98.6** |

Lightweight 구조는 더 빠르고 재현성이 높았으며,  
Ontology 구조의 복잡성이 오히려 학습·추론 과정에 불필요한 잡음을 유발할 수 있음을 보여준다.  
이는 H3 (“Lightweight Graph Ontology는 연구 통제에 적합하다.”)를 실증적으로 지지한다.

---

## Chapter 39. 정성 분석 (Qualitative Analysis)

정량적 지표 외에도, Ontology grounding이 출력 내용의 품질에 어떤 변화를 가져왔는지  
사례 분석을 통해 확인하였다.

**예시 1 – Fatty Liver 케이스**

| Mode | 모델 출력 |
|:--|:--|
| V | “Liver inflammation likely due to infection.” |
| VL | “Increased liver echogenicity, possible fatty changes.” |
| VGL | “Findings indicate hepatic steatosis (fatty liver).” |

Ontology grounding(VGL)은 결과를 보다 정확하고 간결한 의학 용어로 수렴시켰다.  
또한 “hepatic steatosis”와 같은 표준화된 진단명이 consistently 반복되어  
Consistency 향상 결과와 일치했다.

**예시 2 – Multi-finding 케이스**

VGL 모드에서는 다중 관계형 경로(`HAS_FINDING` + `RELATED_TO`)를 활용하여  
두 가지 이상 소견을 논리적으로 연결하는 문장을 생성하였다.  
이것은 Ontology grounding이 LLM에게 **“추론적 연결성(reasoning linkage)”** 을 부여함을 보여준다.

---

## Chapter 40. 결과 요약 및 해석

세 가지 가설(H1–H3)은 모두 실험적으로 지지되었다.

| 가설 | 내용 | 결과 |
|:--|:--|:--|
| H1 | Ontology grounding은 Hallucination을 감소시킨다. | ✅ 유의미하게 감소 (p<0.01) |
| H2 | Ontology grounding은 Consistency를 향상시킨다. | ✅ 유의미하게 증가 (p<0.05) |
| H3 | Lightweight Graph Ontology는 연구 통제에 적합하다. | ✅ Palantir 대비 성능·재현성 우수 |

Ontology grounding은 단순한 정보 보강이 아니라,  
LLM 추론 과정에 **구조적 의미 제약(structural semantic constraint)** 을 도입함으로써  
언어적 불확실성을 줄이고 근거 중심적 응답을 유도한다는 점이 확인되었다.

다음 Part VI에서는 이러한 결과를 인지적·철학적 관점에서 해석하며,  
Ontology 기반 Reasoning이 LLM 신뢰성 연구에 제시하는 새로운 방향을 논의한다.
