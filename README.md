# 🧠 Ontology × vLM × LLM Prototype

## Overview
이 프로젝트는 **LLM이 데이터의 의미를 이해하도록 만드는 방법**을 탐구한다.  
단순한 텍스트 예측을 넘어서, 실제 세계의 구조를 **Ontology(의미 관계)** 와 **vLM(시각적 맥락)** 을 통해 연결하는 것이 목표다.

---

## Why Ontology?
- 데이터 필드나 명칭이 달라도, 같은 개념이라면 **의미적으로 매핑**할 수 있어야 한다.  
- Ontology는 이런 의미적 연결을 제공하고, 모델이 **데이터의 구조와 맥락**을 이해하도록 돕는다.  
- LLM이 만든 결과를 **Neo4j 기반 Knowledge Graph** 에 반영해 일관성을 유지한다.

---

## Why vLM?
- 세상은 텍스트만으로 표현되지 않는다.  
- vLM(Visual Language Model)은 이미지와 텍스트를 함께 이해해, LLM이 **언어 외적 근거(visual grounding)** 를 갖게 한다.  
- 즉, **“보는 것”을 “이해하는 것”으로 연결**한다.

---

## Combined Effect
| 구성 요소 | 역할 |
|------------|------|
| **LLM** | 언어적 추론 |
| **vLM** | 시각적 근거 |
| **Ontology** | 의미적 구조 |

이 세 가지를 결합해, 모델이 단순 언어 모형이 아닌  
**“의미 기반의 통합 지능(Grounded Intelligence)”** 으로 작동하도록 실험한다.

---

## Dataset
- 의료 도메인 더미 데이터 (Patient, Encounter, Observation, Diagnosis, Medication, Image, AIInference)
- Neo4j로 관계형 그래프 구축  
- vLM(VQA/Caption) → LLM 추론 → Ontology 반영 구조

---

## Goal
> 데이터와 언어, 감각이 분리되지 않는 **“이해 가능한 AI”** 를 만드는 첫 단계.

---

## How to Try It

### 1. 건강 상태 확인
```sh
curl http://localhost:8000/health
```

### 2. 동기식 파이프라인 (즉시 응답)
- **VLM + LLM 실행 (그래프 저장 안 함)**
  ```sh
  curl -X POST http://localhost:8000/vision/inference \
    -F "prompt=Summarize the key findings in this X-ray." \
    -F "image=@grounded-ai/data/medical_dummy/images/img_001.png" \
    -F "persist=false"
  ```
- **그래프까지 업서트**
  ```sh
  curl -X POST http://localhost:8000/vision/inference \
    -F "prompt=Summarize the key findings in this image." \
    -F "llm_prompt=Given the vision summary, what should the clinician do next?" \
    -F "image=@grounded-ai/data/medical_dummy/images/img_003.png" \
    -F "modality=XR" \
    -F "patient_id=P9999" \
    -F "encounter_id=E9999" \
    -F "persist=true" \
    -F "idempotency_key=demo-sync-001"
  ```

### 3. 비동기 파이프라인 (Redis Streams + SSE)
1. **작업 생성**
   ```sh
   curl -X POST http://localhost:8000/vision/tasks \
     -F "prompt=Summarize the key findings in this X-ray." \
     -F "llm_prompt=Given the vision summary, what should the clinician do next?" \
     -F "image=@grounded-ai/data/medical_dummy/images/img_001.png" \
     -F "persist=true"
   ```
   → `task_id` / `status_endpoint` 가 응답으로 돌아온다.

2. **상태 스트림 구독 (Server-Sent Events)**
   ```sh
   curl -N http://localhost:8000/vision/tasks/<task_id>/events
   ```
   Redis Streams 기반 워커가 `queued → vision → llm → persisted` 순서로 이벤트를 푸시한다.

### 4. 그래프 질의 샘플
```sh
curl -X POST http://localhost:8000/kg/cypher \
     -H 'Content-Type: application/json' \
     -d '{"query": "MATCH (p:Patient) RETURN p.patient_id LIMIT 5"}'
```
```sh
curl http://localhost:8000/kg/patient/P1005
```
