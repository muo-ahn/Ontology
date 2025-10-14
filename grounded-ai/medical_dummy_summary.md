
# Ontology + vLM + LLM Local Prototype — Medical Dummy Dataset Summary
**Generated:** 2025-10-14 08:16:27

---

## 🧠 목표
- **목표:** LLM + vLM + Ontology 아키텍처를 로컬 머신에서 직접 실험하기 위한 프로토타입 데이터 구축  
- **환경:** RTX 4070 Laptop / Apple M4 Chip (로컬 기반, GPU 및 Metal 모두 지원)

---

## ⚙️ 구성 요약
**전체 구조:**  
```
[Streamlit UI] → [FastAPI Orchestrator]
   ├─ vLM: Qwen2-VL / MiniCPM-V / LLaVA (이미지 캡션 및 VQA)
   ├─ LLM: Qwen2.5-7B-Instruct (Ollama)
   ├─ KG : Neo4j (Ontology 기반 Knowledge Graph)
   └─ VecDB: Qdrant (텍스트·이미지 임베딩 검색)
```

---

## 📊 더미 데이터 생성 내역
경로: `/mnt/data/medical_dummy`

| 파일 | 설명 |
|------|------|
| patients.csv | 환자 정보 (성별, 지역, 생년월일 등) |
| encounters.csv | 입·외래 방문 기록 |
| observations.csv | LOINC 기반 검사 결과 |
| diagnoses.csv | ICD-10 기반 진단 정보 |
| medications.csv | 약물 처방 데이터 |
| imaging.csv | 의료 영상(텍스트 오버레이 이미지 포함) |
| ai_inference.csv | vLM/LLM 추론 결과 |
| ontology_min.json | 온톨로지 구조 정의(JSON) |
| seed.cypher | Neo4j 초기 시드 스크립트 |

---

## 🧩 Ontology 구조 요약
- **엔티티:** Patient, Encounter, Observation, Diagnosis, Medication, Image, AIInference  
- **관계(Relationships):**
  - (Patient)-[:HAS_ENCOUNTER]->(Encounter)
  - (Encounter)-[:HAS_OBS]->(Observation)
  - (Encounter)-[:HAS_DX]->(Diagnosis)
  - (Encounter)-[:HAS_RX]->(Medication)
  - (Encounter)-[:HAS_IMAGE]->(Image)
  - (Image)-[:HAS_INFERENCE]->(AIInference)

---

## 🧪 테스트 아이디어
1. **vLM 질의**: “이 X-ray의 핵심 소견을 한 줄 요약해줘.”  
2. **LLM reasoning**: “이 환자는 추가적으로 어떤 검사가 필요할까?”  
3. **Ontology update**: vLM→LLM 결과를 Neo4j에 반영해 그래프 확장  
4. **복합 질의**: “지난 60일간 고혈압(I10) 진단 + SBP > 140 + 고혈압 약물 복용자”  

---

## 🚀 다음 단계
- Neo4j로 CSV 임포트 및 `seed.cypher` 실행  
- vLM → LLM → Ontology 연결 파이프라인 구현  
- Streamlit UI로 의료 영상 업로드 + 질의/응답 테스트  
- Codex 환경으로 로컬 오케스트레이션 코드 개발

---

_이 파일은 자동 생성된 프로토타입 요약 문서입니다._
