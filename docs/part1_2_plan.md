# 🧭 Part I–II: 서론 및 이론적 배경 (ASC 구조 기반 집필 계획)

본 문서는 논문의 전반부(Part I–II)의 집필 전략을 Aim–Scope–Context(ASC) 구조에 따라 정리한 것이다.

---

## 🎯 A. AIM (연구 목적) — Ch.1~3

### **Ch.1 서론**

- **목적:** LLM의 Hallucination과 Consistency 문제 제기
- **핵심 내용:**
  - LLM의 신뢰성 한계
  - RAG, CoT 등 기존 접근의 제약
  - Ontology grounding의 필요성
  - 연구 질문(RQ1–RQ3)
- **키워드:** hallucination, consistency, reasoning, ontology grounding

---

### **Ch.2 연구 동기**

- **목적:** Palantir Ontology에서 GraphDB 기반으로 전환한 이유 설명
- **핵심 내용:**
  - Palantir Ontology의 장점(표준화, 거버넌스)
  - 연구 관점에서의 한계(통제성, 접근성)
  - 연구형 Lightweight Ontology 개념 정의
- **결론:** GraphDB 기반 접근이 실험 재현성과 구조적 분석에 더 적합함을 논증

---

### **Ch.3 문제 정의**

- **목적:** 연구가 다루는 문제를 수학적·논리적으로 규정
- **핵심 내용:**
  - Hallucination 정의 (fact/logical/common-sense 구분)
  - Consistency 정의 및 평가 필요성
  - 연구 질문 구체화:
    1. Ontology grounding은 Hallucination을 줄이는가?
    2. Ontology grounding은 Consistency를 향상시키는가?
    3. Palantir형 Ontology보다 연구형 Graph Ontology가 효율적인가?

---

## 🧩 B. SCOPE (연구 범위) — Ch.4~5

### **Ch.4 연구 가설**

- **가설 1 (H1):** Ontology 기반 컨텍스트는 Hallucination을 감소시킨다.
- **가설 2 (H2):** Ontology 기반 컨텍스트는 Consistency를 향상시킨다.
- **가설 3 (H3):** Lightweight Graph Ontology는 Palantir Ontology보다 연구 통제에 적합하다.
- **실험적 검증 방법:**
  - 세 모드(V, VL, VGL) 비교
  - HR, CS, PD 지표 기반 평가

---

### **Ch.5 연구 범위 정의**

- **포함 범위:**
  - 의료 dummy dataset 기반 실험
  - LLM inference & graph context reasoning
  - quantitative metrics (HR, CS, PD)
- **제외 범위:**
  - 실제 임상 데이터 활용
  - formal ontology reasoning (OWL, DL 수준)
  - Palantir 플랫폼 내 실험
- **목적:** 연구의 범위와 한계를 명확히 하여 실험적 성격을 강조

---

## 🧠 C. CONTEXT (연구 맥락) — Ch.6~10

### **Ch.6 LLM과 Hallucination**

- LLM의 구조적 한계 (확률적 언어 모델링)
- hallucination taxonomy 및 발생 원인
- 기존 완화 전략(RAG, post-filtering 등)
- 본 연구가 해결하려는 공백 정의

---

### **Ch.7 Ontology와 Knowledge Graph**

- Ontology의 개념: TBox/ABox, formal semantics
- Ontology vs Knowledge Graph 비교
- Palantir Ontology 구조 요약
- Neo4j 기반 Ontology의 장점 (투명성, 실험 통제 가능성)

---

### **Ch.8 GraphRAG 및 Hybrid Reasoning**

- Retrieval-Augmented Generation → GraphRAG 진화 과정
- GraphRAG의 구조적 한계
- Multi-hop reasoning과 path diversity 개념
- 본 연구의 위치: Ontology grounding의 효과를 독립 변수로 검증

---

### **Ch.9 Cognitive 및 Symbolic Reasoning**

- Dual-process theory (System 1 vs System 2)
- Symbolic reasoning과 Ontology의 연결
- LLM + Ontology = Hybrid cognition 모델로서의 의미

---

### **Ch.10 의료 Ontology 사례**

- 의료 데이터에서 Ontology의 필요성
- SNOMED-CT, RadLex, FHIR 요약
- dummy dataset 선택 이유 및 윤리성
- 연구가 갖는 실용적 확장 가능성

---

## 🧩 작성 순서 (추천)

1️⃣ **Aim (Ch.1–3)**: 연구 목적 명료화  
2️⃣ **Scope (Ch.4–5)**: 실험 경계 확정  
3️⃣ **Context (Ch.6–10)**: 기존 연구 대비 공백 정리

---

## 🧩 집필 스타일 가이드

- **논리적 연결**: “왜 Palantir가 아닌가” → “그럼 어떤 Ontology인가” → “그 Ontology가 어떤 효과를 주는가”
- **서술 톤**: 학술적이되 명료하게, 과도한 기술 세부는 뒤 파트로 이연
- **인용 전략**: GraphRAG (2024), Palantir Ontology whitepaper, SNOMED CT, Dual-process theory 논문 참조

---

## 📌 최종 목표

- Part I–II 완성 시, 논문의 30% 골격 확보
- 이후 Part III–V(구현·실험)가 “논리적으로 자연스럽게 따라오는 구조” 형성
