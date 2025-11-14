# Part VI. DISCUSSION AND LIMITATIONS

---

## Chapter 41. 이론적 해석 (Theoretical Interpretation)

본 연구는 Ontology grounding이 LLM의 추론 품질을 향상시킬 수 있음을 실증적으로 확인하였다.  
이 결과를 이론적 관점에서 해석하면, Ontology는 LLM에 **“외부적 의미 제약(external semantic constraint)”** 을 제공한다.  
즉, LLM의 확률적 언어 생성 과정이 그래프 형태의 구조적 근거망을 참조함으로써  
의미적 탐색 공간이 수축되고, 결과적으로 Hallucination이 감소한다.

이 메커니즘은 **Neural–Symbolic Hybrid Reasoning** 의 고전적 관점과 일치한다.  
LLM의 연상 기반(System 1) 추론 위에 Ontology라는 형식적(System 2) 구조가 결합되어  
언어 생성이 보다 논리적이고 근거 중심적으로 진화한다.  
이는 단순한 데이터 보강이 아니라, **인지적 협동(cognitive cooperation)** 의 한 형태로 해석될 수 있다.

결과적으로 본 연구는 Ontology grounding이 LLM 신뢰성 향상의 실질적 방법임을 보여줌과 동시에,  
LLM을 단순한 통계 모델이 아닌, “구조적 지식을 내재화할 수 있는 학습체계”로 확장할 가능성을 시사한다.

---

## Chapter 42. Palantir Ontology와의 철학적 비교 (Philosophical Comparison)

Palantir Ontology는 엔터프라이즈 환경에서 **데이터 통합과 관리의 완전성** 을 지향한다.  
이는 실무적으로 강력하지만, 연구 관점에서는 지나치게 “닫힌 체계(closed system)”이다.  
모든 개체와 관계가 명시적으로 정의되어 있어, 새로운 가설이나 개념의 탐색이 어렵다.

반면 본 연구에서 제안한 **Lightweight Graph Ontology** 는  
지식의 완전성보다는 **실험 통제성과 확장성** 을 중시한다.  
이는 Ontology를 “사실의 총합”이 아닌 “추론 가능성을 검증하는 실험 도구”로 재정의한 시도라 할 수 있다.

철학적으로 이는 **Platonism(절대적 실재)** 에 가까운 Palantir 접근과,  
**Constructivism(실험적 구성)** 에 가까운 Graph Ontology 접근 간의 대비로 해석된다.  
전자는 완전한 세계를 기술하려 하고, 후자는 세계의 일관성을 검증하려 한다.  
따라서 본 연구의 Ontology는 “기술(description)”이 아니라 “검증(test)”의 수단이다.

---

## Chapter 43. 연구의 한계 (Limitations)

본 연구는 실험 설계와 결과 해석에서 다음과 같은 한계를 가진다.

1. **데이터 규모의 제약**  
   - Dummy dataset을 사용했기 때문에, 실제 임상 데이터의 복잡성을 완전히 반영하지 못함.  
   - 실제 상황에서는 다의적·모호한 표현이 더 자주 등장할 수 있음.

2. **Formal reasoning 부재**  
   - 본 연구의 Ontology는 관계 기반 그래프 모델이며,  
     OWL이나 Description Logic 수준의 추론 엔진을 포함하지 않는다.  
   - 따라서 논리적 완전성(Logical completeness)은 제한적이다.

3. **모델 다양성 부족**  
   - GPT 계열 모델에 한정된 결과로, 다른 LLM(Claude, Gemini 등)에 대한 일반화가 필요함.

4. **Prompt bias 가능성**  
   - Graph context가 항상 동일 순서로 제공되어, LLM의 attention 분포에 일정한 bias가 존재할 수 있음.

5. **통계적 검증 한계**  
   - 표본 수가 제한적이므로, p-value의 절대적 신뢰성보다는 경향성 중심의 해석이 필요함.

이러한 한계는 본 연구의 결과가 “완결적” 결론이 아닌  
“추후 Formal Ontology·대규모 데이터 기반 확장의 출발점”임을 의미한다.

---

## Chapter 44. 재현성과 일반화 (Reproducibility and Generalization)

본 연구의 가장 큰 강점 중 하나는 **재현 가능한 파이프라인 구조** 다.  
Vision → Graph → Language → Evaluation의 전체 과정이 Docker 및 Redis Stream을 통해  
완전 자동화되어 있으며, 환경 변수 및 모델 seed가 통제된다.  

이 구조는 다른 도메인에서도 재사용 가능하다.  
예를 들어 법률 문서 해석이나 산업 안전 보고서 분석에서도  
Ontology grounding은 유사한 신뢰성 개선 효과를 가질 수 있다.  

다만, Ontology 구조는 도메인별 개념 체계에 따라 조정되어야 하며,  
본 연구의 의료 Ontology를 직접 재사용하기보다는  
**Ontology 설계 원칙 (노드 단순화, 관계 명시화, path 제한)** 을 참조하는 형태가 적합하다.

---

## Chapter 45. 시스템 확장성 논의 (System Scalability)

본 연구는 실험용 파이프라인을 중심으로 설계되었기 때문에  
대규모 환경에서는 다음의 개선이 필요하다.

1. **Redis Stream의 한계**  
   - 1000 이벤트 수준에서는 안정적이지만,  
     초당 수천 이벤트 처리 시 consumer lag 증가.  
   - Kafka, AWS Kinesis 등 대체 구조 고려 가능.

2. **Neo4j 병렬 처리 한계**  
   - Write-heavy 작업에서 I/O 병목 발생 가능.  
   - Read replica 및 shard 구조로 확장 필요.

3. **LLM 호출 병렬화**  
   - API latency가 전체 파이프라인 병목의 70%를 차지.  
   - Async batch inference로 병렬화 필요.

이러한 개선을 통해 실험용 시스템을 연구 플랫폼 수준으로 확장할 수 있다.

---

## Chapter 46. Explainability와 Human Trust의 관점 (Explainability and Human Trust)

Ontology grounding은 단순히 모델의 성능을 높이는 기술적 수단이 아니라,  
**인간이 모델을 신뢰할 수 있는 근거 구조를 시각화하는 메커니즘** 이기도 하다.  

Ontology는 LLM의 “설명 가능한 경로(explanatory path)”를 명시한다.  
모델의 응답을 그래프 경로로 추적할 수 있음으로써,  
사용자는 LLM의 판단 근거를 직관적으로 이해할 수 있다.  
이는 “black-box”로 비판받던 LLM을 **“semi-transparent reasoning system”** 으로 전환시킨다.

인지심리학적으로, 사람은 일관된 근거망을 제시하는 시스템에 더 높은 신뢰를 부여한다.  
본 연구의 VGL 파이프라인은 Hallucination을 줄이는 동시에  
그 “감소 이유”를 시각적으로 설명할 수 있게 한다는 점에서  
Explainable AI(XAI)의 새로운 형태를 제시한다.

---

### 요약

Part VI는 Ontology grounding이 LLM 신뢰성 향상에 미치는  
인지적·철학적 의미를 해석하고, 연구의 한계를 투명하게 제시했다.  
이제 Part VII에서 본 연구의 결론, 학문적 기여,  
그리고 향후 확장 방향(Formal reasoning, human evaluation 등)을 정리한다.
