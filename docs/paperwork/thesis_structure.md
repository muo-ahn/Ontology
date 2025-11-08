# 📘 전체 논문 구성 (50장 규모)

본 문서는 “Ontology 기반 Reasoning을 통한 LLM Hallucination 완화 연구”의 전체 논문 구성 계획이다.  
총 50장으로 구성되며, Palantir Ontology에서 GraphDB 기반 Lightweight Ontology로 전환한 연구 배경을 중심으로 서술한다.

---

## 🧭 전체 개요

| 파트     | 장 수 | 주요 내용                |
| -------- | ----- | ------------------------ |
| Part I   | 5장   | 서론 및 연구 배경        |
| Part II  | 5장   | 관련 연구 및 이론적 근거 |
| Part III | 10장  | 시스템 설계 및 구현      |
| Part IV  | 10장  | 실험 설계 및 데이터셋    |
| Part V   | 10장  | 실험 결과 및 분석        |
| Part VI  | 6장   | 논의 및 한계             |
| Part VII | 4장   | 결론 및 향후 연구        |

총 약 25,000단어 (A4 약 50장 분량)

---

## 🧱 Part I. 서론 및 연구 배경 (Ch.1–5)

| 장  | 제목      | 핵심 내용                           |
| --- | --------- | ----------------------------------- |
| 1   | 서론      | 연구 목적, 배경, 문제의식           |
| 2   | 연구 동기 | Palantir → Graph Ontology 전환 배경 |
| 3   | 문제 정의 | Hallucination·Consistency 문제 규정 |
| 4   | 연구 가설 | Ontology 효과에 대한 가설 3가지     |
| 5   | 연구 범위 | 포함·제외 범위, 연구 한계 정의      |

---

## 📚 Part II. 관련 연구 및 이론적 배경 (Ch.6–10)

| 장  | 제목                                      | 핵심 내용                       |
| --- | ----------------------------------------- | ------------------------------- |
| 6   | LLM과 Hallucination                       | LLM 한계와 환각 문제            |
| 7   | Ontology와 Knowledge Graph                | 정의, 구조, 차이점              |
| 8   | GraphRAG 및 Hybrid Reasoning              | 관련 기술 동향                  |
| 9   | Cognitive Grounding 및 Symbolic Reasoning | 인지이론적 근거                 |
| 10  | 의료 Ontology 응용 사례                   | 실제 도메인 연결 및 윤리적 고려 |

---

## ⚙️ Part III. 시스템 설계 및 구현 (Ch.11–20)

- 전체 아키텍처
- Vision Layer (VLM caption)
- Graph Context Generator
- Redis Stream 파이프라인
- LLM Layer (V/VL/VGL)
- Evaluation Engine
- 시각화 및 환경 구성

---

## 🔬 Part IV. 실험 설계 및 데이터셋 (Ch.21–30)

- 실험 목적 및 변수 정의
- Dummy Dataset 구조
- 평가 지표 (HR, CS, PD)
- Palantir vs Graph 비교 실험
- Ablation Study (Depth, SNOMED 등)
- CI/CD 자동화 및 윤리적 고려

---

## 📊 Part V. 실험 결과 및 분석 (Ch.31–40)

- 정량적 결과 (HR/CS/PD)
- 정성 분석 (응답 예시)
- Prompt 형식 효과
- Depth / Mapping ablation
- Palantir 모사형 비교

---

## 🧩 Part VI. 논의 및 한계 (Ch.41–46)

- 이론적 해석
- Palantir 대비 철학적 비교
- 시스템 확장성
- 재현성·일반화 논의
- Explainability 측면 논의

---

## 🧠 Part VII. 결론 및 향후 연구 (Ch.47–50)

- 연구 요약 및 기여
- 학문적·산업적 의미
- 향후 연구 방향 (Formal reasoning, SNOMED 통합, Cognitive evaluation)
- 부록(데이터, 코드, 시각화 결과 등)

---

## 🧩 부록 (Appendices)

- A. Graph Schema 및 Cypher Seed
- B. Evaluation 코드
- C. Metrics 수식
- D. Prompt Template
- E. 실험 로그 및 전체 결과표
