# 의료 더미 데이터 요약 (Ontology + vLM + LLM 프로토타입)

**생성 시각:** 2025-10-14 08:16:27  
**목표:** 로컬 환경에서 멀티모달 추론 파이프라인을 검증하기 위한 의료 더미 데이터셋 소개

---

## ⚙️ 전체 구성
```
[Streamlit UI] → [FastAPI Orchestrator]
   ├─ vLM: Qwen2-VL / MiniCPM-V / LLaVA
   ├─ LLM: Qwen2.5-7B-Instruct (Ollama)
   ├─ KG : Neo4j
   └─ VecDB: Qdrant
```

## 📁 데이터 위치
- 기본 경로: `/mnt/data/medical_dummy`

| 파일 | 설명 |
|------|------|
| `patients.csv` | 환자 정보(성별, 지역, 생년월일 등) |
| `encounters.csv` | 방문 이력 |
| `observations.csv` | LOINC 기반 검사 결과 |
| `diagnoses.csv` | ICD-10 진단 정보 |
| `medications.csv` | 처방 약물 |
| `imaging.csv` | 의료 영상 메타데이터 (캡션 포함) |
| `ai_inference.csv` | vLM/LLM 추론 결과 |
| `ontology_min.json` | 최소 온톨로지 스키마 |
| `seed.cypher` | Neo4j 초기화 스크립트 |

## 🧩 온톨로지 구조
- **노드 유형**: Patient, Encounter, Observation, Diagnosis, Medication, Image, AIInference
- **관계**:
  - (Patient)-[:HAS_ENCOUNTER]->(Encounter)
  - (Encounter)-[:HAS_OBS]->(Observation)
  - (Encounter)-[:HAS_DX]->(Diagnosis)
  - (Encounter)-[:HAS_RX]->(Medication)
  - (Encounter)-[:HAS_IMAGE]->(Image)
  - (Image)-[:HAS_INFERENCE]->(AIInference)

## 🔬 실험 아이디어
1. vLM: “이 X-ray의 핵심 소견은 무엇인가?”
2. LLM: “추가로 필요한 검사/조치는?”
3. Ontology 업데이트: vLM→LLM 결과를 Neo4j에 기록.
4. 복합 질의: `I10` + `SBP > 140` + 항고혈압제 처방 환자 찾기.

## 🚀 다음 단계
- Neo4j로 CSV 임포트 후 `seed.cypher` 실행.
- FastAPI에서 vLM → LLM → Ontology 파이프라인을 연결.
- Streamlit UI로 업로드·Q&A 워크플로 실험.
- Codex를 활용해 로컬 오케스트레이션/프롬프트를 반복 개선.

---

_이 문서는 자동 생성된 요약을 바탕으로 한글로 재작성한 버전입니다._
