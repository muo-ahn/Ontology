# 그래프 DB 모델링 설계 – 2025-10-22

## 1. 범위
- 멀티모달 추론 파이프라인을 뒷받침하는 Neo4j 온톨로지를 정비한다.  
- 스키마 정규화, 버전 관리, 데이터 품질 보호에 집중하면서 기존 적재 흐름을 중단하지 않는다.  
- 개발자가 단계적 마이그레이션을 수행할 수 있도록 설계 산출물을 제공한다.

## 2. 현 문제점
1. **느슨한 관계**: AIInference 노드가 명확한 출처나 내원 맥락 없이 존재하는 경우가 있음.  
2. **시간 정보 불명확**: 관찰, 진단, 영상 이벤트에 일관된 시간 속성이 없다.  
3. **스키마 드리프트**: 스키마 수정이 임시 대응으로 진행되어 변경 이력이 없다.  
4. **데이터 무결성 위험**: 제약 부족으로 고아 노드/중복 식별자가 발생할 수 있다.

## 3. 목표 온톨로지 구조
### 3.1 핵심 엔티티
| 라벨 | 주요 속성 | 비고 |
|------|-----------|------|
| `Patient` | `patient_id`, 인구통계 | 환자당 유일 |
| `Encounter` | `encounter_id`, `start_at`, `end_at`, `type` | `Patient`와 연결 |
| `Observation` | `observation_id`, `loinc_code`, `value`, `unit`, `observed_at` | `reference_range` 선택 |
| `Diagnosis` | `diagnosis_id`, `icd_code`, `confidence`, `recorded_at` | 임상/추론 구분 |
| `Procedure` | `procedure_id`, `cpt_code`, `performed_at` | 시술 정보 추가 |
| `Medication` | `med_id`, `drug_name`, `dose`, `route`, `schedule` | |
| `Image` | `image_id`, `modality`, `captured_at`, `storage_uri` | |
| `AIInference` | `inference_id`, `model`, `task`, `timestamp`, `version` | 입력·출력과 연결 |
| `OntologyVersion` | `version_id`, `applied_at`, `description` | 스키마 계보 |

### 3.2 관계
- `(Patient)-[:HAS_ENCOUNTER]->(Encounter)`  
- `(Encounter)-[:HAS_OBSERVATION]->(Observation)`  
- `(Encounter)-[:HAS_DIAGNOSIS]->(Diagnosis)`  
- `(Encounter)-[:HAS_PROCEDURE]->(Procedure)`  
- `(Encounter)-[:HAS_MEDICATION]->(Medication)`  
- `(Encounter)-[:HAS_IMAGE]->(Image)`  
- `(Image)-[:HAS_INFERENCE {role:'vision'}]->(AIInference)`  
- `(Encounter)-[:HAS_INFERENCE {role:'llm'}]->(AIInference)`  
- `(AIInference)-[:DERIVES_FROM]->(Observation|Diagnosis|Procedure|Image)` (출처)  
- `(AIInference)-[:RECORDED_WITH]->(OntologyVersion)`  
- `(Observation|Diagnosis|Procedure)-[:VERIFIED_BY]->(AIInference)` (옵션 피드백)

### 3.3 시간 모델링
- 노드에 `*_at` 또는 `*_start/_end` 타임스탬프를 저장한다.  
- 기간을 갖는 이벤트(약물 투여)는 `VALID_DURING` 관계로 표현한다.  
- `EncounterTimeline` 쿼리 템플릿을 두어 연대기 뷰를 조합한다.

## 4. 버전 관리 및 마이그레이션 전략
1. **스키마 레지스트리**  
   - `/schema/vX_Y/constraints.cypher`, `migrations.cypher` 파일로 버전 관리.  
   - 각 마이그레이션이 선행 조건을 선언하고 `OntologyVersion` 노드를 기록한다.
2. **이중 기록 기간**  
   - 롤아웃 중에는 기존 구조와 신구 구조에 동시 기록(가능한 경우)하고 버전 태그를 붙인다.
3. **검증 단계**  
   - 기존 vs 신규 표현을 비교하는 일관성 검사를 수행한 뒤 레거시 엣지를 제거한다.
4. **롤백 계획**  
   - 실패 시 이전 스키마로 돌아갈 `migrations_down.cypher` 스크립트를 제공한다.

## 5. 데이터 품질 제어
1. **제약 조건**  
   - 모든 기본 ID에 유일성 제약, 중요 속성에 존재 제약을 적용한다.  
   - 예: `MATCH (i:AIInference) WHERE i.model IS NULL RETURN i`는 0건이어야 한다.
2. **자동 점검**  
   - 고아 노드, 겹치는 타임라인, 누락된 출처를 탐지하는 Cypher 레시피를 제공한다.  
   - Docker 기반 통합 테스트를 CI에서 실행한다.
3. **메타데이터 로깅**  
   - 각 적재 배치가 성공/실패 카운트, 제약 위반 등을 `IngestionAudit` 노드에 기록한다.

## 6. 구현 계획(상위 레벨)
1. **스키마 작성** – 신규 노드/관계, 제약, 인덱스에 대한 Cypher 초안을 만든다.  
2. **픽스처 업데이트** – 시술, 확장 약물 필드, AI 추론 출처 컬럼을 포함하도록 CSV 및 `seed.cypher`를 수정한다.  
3. **마이그레이션 스크립트** – 상향/하향 스크립트를 버전 ID와 함께 작성한다.  
4. **적재 서비스 리팩터링** – Python 서비스/워커에서 새로운 관계와 출처 데이터를 채우도록 조정한다.  
5. **검증 스위트** – Pytest 통합 테스트를 작성해 제약과 샘플 쿼리를 확인한다.  
6. **문서화** – 다이어그램과 온보딩 노트를 작성한다.

## 7. 오픈 이슈
- AI 추론 출처에 모델 하이퍼파라미터까지 포함해야 할까?  
- 이미 커밋된 AI 추론과 늦게 들어오는 임상 사실이 충돌할 때 어떻게 조정할까?  
- 온톨로지 버전의 보존 정책은 어떻게 설정할까?  
- 이번 단계에서 다중 기관(멀티 테넌시) 지원이 필요한가?
