# 온톨로지 + vLM + LLM 로컬 프로토타입

## 개요
- 온톨로지, 비전 언어 모델(vLM), 대형 언어 모델(LLM) 조합을 로컬 워크스테이션에서 실험하기 위한 프로토타입입니다.
- 타깃 하드웨어: RTX 4070 Laptop GPU, Apple Silicon(M4) + Metal 가속.
- 목표: Streamlit UI → FastAPI → vLM → LLM → Neo4j/Qdrant로 이어지는 전체 파이프라인을 의학 더미 데이터로 검증.

## 시스템 아키텍처
```
[Streamlit UI] → [FastAPI Orchestrator]
   ├─ vLM: Qwen2-VL / MiniCPM-V / LLaVA (캡션 + VQA)
   ├─ LLM: Qwen2.5-7B-Instruct (Ollama)
   ├─ KG : Neo4j (온톨로지 기반 지식 그래프)
   └─ VecDB: Qdrant (텍스트·이미지 임베딩 검색)
```

## 더미 데이터셋 (`/mnt/data/medical_dummy`)
| 파일 | 설명 |
|------|------|
| `patients.csv` | 환자 인구통계 |
| `encounters.csv` | 입·외래 방문 기록 |
| `observations.csv` | LOINC 기반 검사 결과 |
| `diagnoses.csv` | ICD-10 진단 정보 |
| `medications.csv` | 처방 약물 |
| `imaging.csv` | 영상 메타데이터 및 캡션 |
| `ai_inference.csv` | vLM/LLM 추론 결과 |
| `ontology_min.json` | 온톨로지 스키마 스냅샷 |
| `seed.cypher` | Neo4j 초기 시드 스크립트 |

## 온톨로지 개요
- **엔티티**: Patient, Encounter, Observation, Diagnosis, Medication, Image, AIInference
- **관계**
  - (Patient)-[:HAS_ENCOUNTER]->(Encounter)
  - (Encounter)-[:HAS_OBS]->(Observation)
  - (Encounter)-[:HAS_DX]->(Diagnosis)
  - (Encounter)-[:HAS_RX]->(Medication)
  - (Encounter)-[:HAS_IMAGE]->(Image)
  - (Image)-[:HAS_INFERENCE]->(AIInference)

## 실험 플레이북
1. vLM 프롬프트: “이 X-ray를 요약해줘.”
2. LLM reasoning: “추가 검사나 조치는 무엇이 필요한가?”
3. 온톨로지 업데이트: vLM→LLM 결과를 Neo4j에 반영.
4. 복합 질의: “최근 60일 내 I10 + SBP > 140 + 항고혈압제 처방 환자 찾기.”

## 향후 과제
1. **레이트 퓨전**: LLM·VLM 추론 경로를 분리 유지하면서 프롬프트 기반 통합 품질을 다듬는다.
2. **미드 퓨전**: 두 모델의 잠재 표현을 정렬할 수 있도록 크로스 어텐션/비주얼 토큰 주입 프로토타입을 시도한다.
3. **그래프 DB 모델링**: Neo4j 스키마, 제약, 데이터 검증 절차를 개선해 온톨로지 일관성을 강화한다.
