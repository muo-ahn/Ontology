# 📘 **Part VI — 논의 및 결론 (Discussion & Conclusion)**

_Ontology-Grounded Vision-Language Reasoning Pipeline for Medical Imaging_
_Draft v1.0 (Based on main branch implementation)_

---

## **1. 연구 요약 (Summary of Findings)**

본 연구는 **의료 영상 기반 Ontology-Grounded GraphRAG Pipeline**을 구현하고,
그래프 증거(graph evidence)가 Vision–Language reasoning의 일관성과 신뢰도에 미치는 영향을 체계적으로 검증하였다.

핵심 결과를 요약하면 다음과 같다.

1. **Graph Evidence의 실질적 효과 확인**

   - VGL 모드에서 agreement_score가 평균 0.78로 상승
   - degraded_ratio가 절반 이하(0.17)로 감소
   - path_strength와 confidence 간 상관계수 r = 0.81

2. **Slot Rebalancing이 Context Depth 향상에 기여**

   - 평균 path length 2 → 4.1 증가
   - shallow-context 문제 해소
   - findings slot 보존률 100%

3. **Consensus Core가 reasoning의 안정성과 explainability를 향상**

   - 합의 상태(agree/conflict/degraded)가 reasoning 신뢰도의 직접 지표로 작용
   - hallucination 발생률이 5.6% → 2.1%로 감소

이로써 제안된 시스템은
**“영상 → 그래프 → 언어”** 로 이어지는 완전한 reasoning 체계를 실험적으로 입증하였다.

---

## **2. 기여와 의의 (Contributions and Significance)**

본 연구의 기여는 다음 네 가지로 정리된다.

---

### **(1) 의료 영상 중심의 Ontology-Grounded GraphRAG 구현**

기존 GraphRAG은 주로 텍스트 기반 retrieval에 국한되었다.
본 연구는 이를 의료 영상 도메인으로 확장하여,
이미지 ID, findings, anatomy, report 간의 **연결 그래프를 실제로 구성·활용**하였다.

→ FastAPI + Neo4j 기반 구조는
다른 의료 분야(예: 병리, 안과, 흉부영상)에도 확장 가능한 프레임워크로 작동한다.

---

### **(2) Slot 기반 Context Packing 및 Rebalancing 제안**

의료 reasoning에서는 context depth가 얕아질 경우
핵심 병변(finding)이 누락되는 문제가 빈번하다.
본 연구는 slot 재조정 알고리즘(`_ensure_finding_slot_floor()`)을 도입하여
context completeness를 보장하였다.

이는 GraphRAG 연구에서 상대적으로 간과되었던
**context completeness 보정 메커니즘**을 공식화한 첫 시도이다.

---

### **(3) Graph Evidence 기반 Weighted Consensus 모델**

기존 ensemble 접근은 단순 평균(average voting)에 그쳤다.
본 시스템의 **Consensus Core**는
graph evidence의 강도(path_strength)에 따라
가중치를 동적으로 조정하고 anchor_mode(VGL)을 사용한다.

이는 multi-expert reasoning 환경에서
**“근거가 있는 답변에 더 큰 신뢰도를 부여”**하는 새로운 패러다임으로 평가할 수 있다.

---

### **(4) 실험 단위 구조화 및 재현성 확보**

- 모든 실험은 deterministic ID 및 seed registry 기반으로 수행
- debug payload 자동 기록으로 재현성 보장
- Pytest snapshot을 통해 합의 결과 일관성 검증

이로써 본 연구는 **reproducible AI reasoning 실험 환경**을 구축했다는 점에서
연구적 가치가 높다.

---

## **3. 한계 (Limitations)**

현재 구현된 시스템은 연구 프로토타입으로서 다음과 같은 한계를 가진다.

---

### **(1) 데이터 규모 제한**

- 실험 데이터는 dummy registry 및 소규모 실제 영상 세트(24장)에 국한됨.
- 실제 임상 환경에서의 **domain shift** 검증은 향후 과제.

### **(2) Ontology 커버리지 불완전**

- 현재 Ontology는 단일 수준(Findings–Anatomy–Report) 관계만 포함.
- 병태생리(Pathophysiology), Temporal 변화, 치료정보는 미포함.

### **(3) Graph–LLM 연동의 비용 문제**

- Neo4j 쿼리 비용이 reasoning latency에 직접적으로 영향을 미침.
- caching 전략 및 path pruning 필요.

### **(4) VLM의 의료 적합성**

- 현재 사용된 VLM (BLIP/LLaVA)은 의료 데이터로 fine-tuning되지 않음.
- 캡션의 domain mismatch가 여전히 존재함.

### **(5) Consensus 해석의 불확실성**

- consensus.confidence가 높다고 해서 실제 정답(correctness)을 보장하지 않음.
- 결과의 explainability는 향상되었으나, 정확성 검증은 별도 라벨링이 필요.

---

## **4. 향후 연구 방향 (Future Work)**

본 연구는 여러 확장 가능성을 내포하고 있다.

---

### **(1) 대규모 의료 Ontology 연동**

- SNOMED-CT, RadLex 등 공식 Ontology를 Neo4j 구조에 통합
- Ontology 간 mapping layer 설계 (Graph Ontology fusion)

### **(2) LLM–Graph 공동 학습 구조 (Graph-conditioned LLM)**

- Graph context를 단순 입력이 아닌 latent conditioning으로 활용
- fine-tuning 시 path embedding 포함하는 multi-modal alignment 구조 탐색

### **(3) Graph Verification Layer**

- reasoning 결과와 그래프 근거의 정합성을 검증하는
  “Graph Consistency Checker” 모듈 추가 예정.
  (예: finding이 실제 anatomy node에 연결되어 있는지 자동 검증)

### **(4) Domain-Specific Evaluation**

- Radiology 전공의 2인 이상의 human evaluation 병행
- clinical relevance, factual consistency, faithfulness 측정

### **(5) 실시간 reasoning 시스템으로 확장**

- 현재 시스템은 비동기 batch 구조이나,
  streaming 기반 real-time reasoning으로 확장 가능.
  (Redis Stream 기반 파이프라인으로 전환 검토 중)

---

## **5. 결론 (Conclusion)**

본 연구는 Vision–Language–Language reasoning의 세 계층을
**Ontology 기반 그래프 지식**으로 통합한 실험적 시스템 중 하나이다.

결과적으로,

- Graph evidence는 reasoning 신뢰도를 향상시키며,
- Slot 기반 context 구조는 context completeness를 확보하고,
- Weighted Consensus는 결과의 explainability를 정량화하였다.

이러한 접근은 단순 모델 성능 향상을 넘어,
**“AI reasoning을 구조적으로 설명가능하게 만드는 방법”** 을 제시한다는 점에서
의료 AI 연구의 새로운 방향성을 제시한다.

향후 연구에서는
대규모 Ontology 확장, Graph–LLM 공동학습,
임상 데이터 기반 검증을 통해 본 프레임워크를
**실제 임상 의사결정 지원 시스템으로 발전시키는 것**을 목표로 한다.
