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

### 2. vLM 캡션 정규화 (이미지 → JSON)
```sh
curl -X POST http://localhost:8000/vision/caption \
  -H "Content-Type: application/json" \
  -d '{
        "file_path": "grounded-ai/data/medical_dummy/images/img_001.png",
        "image_id": "IMG_001"
      }'
```
- 응답: `image`, `report`, `findings[]` 필드를 포함한 표준 JSON.

### 3. 그래프 업서트 (노드 + 엣지 강제 생성)
```sh
curl -X POST http://localhost:8000/kg/upsert \
  -H "Content-Type: application/json" \
  -d '{
        "case_id": "CASE_DEMO_001",
        "image": {
          "image_id": "IMG_001",
          "path": "/data/img_001.png",
          "modality": "XR"
        },
        "report": {
          "id": "rep_demo_001",
          "text": "Chest X-ray – probable RUL nodule (~1.8 cm).",
          "model": "qwen2-vl",
          "conf": 0.83,
          "ts": "2025-10-23T12:00:00Z"
        },
        "findings": [
          {
            "id": "find_demo_001",
            "type": "nodule",
            "location": "RUL",
            "size_cm": 1.8,
            "conf": 0.87
          }
        ]
      }'
```
- `HAS_IMAGE`, `HAS_FINDING`, `DESCRIBED_BY` 엣지가 모두 생성되는지 확인할 수 있다.

### 4. 그래프 컨텍스트 조회 (엣지 기반 직렬화)
```sh
curl "http://localhost:8000/kg/context?image_id=IMG_001"
```
- 응답: `findings`, `reports` 뿐 아니라 사람이 읽기 좋은 `triples[]` 포함.

### 5. LLM 최종 소견 (V / VL / VGL 비교)
```sh
curl -X POST http://localhost:8000/llm/answer \
  -H "Content-Type: application/json" \
  -d '{"mode": "VGL", "image_id": "IMG_001", "style": "one_line"}'
```
- `mode` 조회: `V`(vLM 캡션), `VL`(캡션→LLM), `VGL`(그래프 컨텍스트 기반).

### 6. 비동기 파이프라인 (선택)
1. **작업 생성**
   ```sh
   curl -X POST http://localhost:8000/vision/tasks \
     -F "prompt=Summarize the key findings in this X-ray." \
     -F "image=@grounded-ai/data/medical_dummy/images/img_002.png" \
     -F "persist=true"
   ```
2. **SSE 스트림 감시**
   ```sh
   curl -N http://localhost:8000/vision/tasks/<task_id>/events
   ```
   → Redis Streams 워커가 `queued → vision → graph → llm` 순으로 이벤트를 보낸다.

### 7. 추가 그래프 질의
```sh
curl -X POST http://localhost:8000/kg/cypher \
  -H "Content-Type: application/json" \
  -d '{"query": "MATCH (i:Image)-[r:HAS_FINDING]->(f:Finding) RETURN i.id AS image, f.type AS finding LIMIT 5"}'
```
