# 📊 문제 요약 테이블 (확정 상태)

| ID    | 분류                     | 상태   | 핵심 원인 또는 결함                                          | 주요 근거 또는 파일                                                                            | 즉시 영향                            | 권장 조치 요약                                     |
| ----- | ---------------------- | ---- | ---------------------------------------------------- | -------------------------------------------------------------------------------------- | -------------------------------- | -------------------------------------------- |
| **A** | `Image` 스키마 불일치        | ✅ 확정 | 제약 키는 `id` 인데 MERGE는 `image_id` 사용                   | `seed.cypher` – `CREATE CONSTRAINT ... img.id IS UNIQUE` vs `MERGE (img {image_id:…})` | 중복 노드 허용 → 데이터 정합성 붕괴            | 제약을 `image_id`로 맞추거나 MERGE 키를 `id:`로 통일      |
| **B** | 모달리티/캡션 불일치            | ✅ 확정 | modality:`ECG` ↔ caption:`Abdominal ultrasound`      | `seed.cypher` – `IMG_002` 레코드                                                          | 그래프 노이즈 → 모델 정합성 왜곡              | 시드 데이터 정정 또는 ECG 별도 라벨                       |
| **C** | 버전 필드 불일치              | ✅ 확정 | `OntologyVersion.version_id` ↔ `AIInference.version` | `seed.cypher` – `SET ai += {…, version:'1.1'}`                                         | 조인/트래킹 혼선                        | 명칭 통일(`version_id` 또는 `version`)             |
| **D** | 앙상블 합의 부재              | ✅ 확정 | V/VL/VGL 독립 실행, VGL→VL 폴백만 존재                        | `pipeline.py:322–387`, `llm.py:108–163`                                                | 모드 불일치 시 불안정 결과 노출               | 2/3 합의·가중치 스코어링·불일치 시 낮은 확신 다운그레이드           |
| **E** | 컨텍스트 PATH 미구현 및 반환 불일치 | ✅ 확정 | Cypher는 `hits` 반환, `paths` 키 미존재 → 항상 빈 리스트          | `graph_repo.py:127`, `context_pack.py:36–199`                                          | `.context_paths_len=0`, 설명가능성 결여 | `MATCH (p=...)` 경로 쿼리 신설 + `RETURN paths` 통일 |
| **F** | 테스트/CI 체계 미비           | ✅ 확정 | pytest 부재, 수동 스크립트만 존재                               | `tests/integration/test_graph_migrations.py`, `scripts/test_endpoints.py`              | 리그레션 탐지 불가 / 품질 보장 없음            | pytest 골든 스냅샷 + CI 워크플로우 도입                  |

---

# 🧩 상세 리포트

## **A. Image 제약 키 불일치**

* **원인**
  `seed.cypher`에서 `CREATE CONSTRAINT FOR (img:Image) REQUIRE img.id IS UNIQUE`
  하지만 `MERGE (img:Image {image_id:…})` → 제약이 실제 작동 안 함.
* **영향**
  중복 이미지 노드 생성 가능, 업서트/조회 시 정합성 붕괴.
* **조치안**

  * 제약을 `img.image_id`로 변경 또는 MERGE 키를 `id:`로 통일.
  * 스키마/업서트/조회 전 구간 동일 키 정의.

---

## **B. 모달리티/캡션 불일치**

* **원인**
  `IMG_002` 레코드: `modality:'ECG'` + `caption_hint:'Abdominal ultrasound – fatty liver'`.
  즉 ECG 이미지를 초음파 캡션으로 기록.
* **영향**

  * 그래프 노드 간 모달리티 정합성 붕괴 → 후속 AI 모델 혼선.
  * 모달리티 기반 탐색/통계 결과 오류.
* **조치안**

  * `modality:'US'`로 수정 또는 ECG는 별도 노드 타입으로 관리.
  * 데이터 입력 시 모달리티-캡션 정합성 검증 루틴 추가.

---

## **C. 버전 필드 불일치**

* **원인**
  `OntologyVersion` 노드는 `version_id`, `AIInference`는 `version` 사용.
* **영향**
  버전 조인 실패, 변경 추적 불일치.
* **조치안**

  * 명칭 단일화(`version_id`).
  * 모든 Cypher 및 Python 코드에서 해당 필드 통일.

---

## **D. 앙상블 합의/거부 로직 부재**

* **확인 결과**
  `/pipeline/analyze` 는 V/VL/VGL 결과를 **독립적으로 실행** 후 **VGL 실패 시 VL로 폴백**.
  `run_eval.py`는 후처리용으로만 비교(서빙 비포함).
  → **합의, 투표, 스코어링 로직 부재.**
* **영향**
  모드 결과 불일치 시 사용자 출력에 모순 발생.
* **조치안**

  * 2/3 투표 또는 유사도 가중치 스코어링.
  * 파일명·메타 히스토그램 가중치 추가.
  * 불일치 시 “낮은 확신” 접두어 및 디버그 로그 기록.

---

## **E. 컨텍스트 PATH 탐색 미구현**

* **확인 결과**
  `GraphRepo.query_paths()` → Cypher 결과의 `hits` 필드 사용, `paths` 없음 → 항상 빈 리스트.
  실제 쿼리에는 `MATCH (p=...)` 경로 탐색 부재.
* **영향**

  * `.context_paths_len=0` 지속.
  * 설명가능성 및 근거 추적 기능 실질적으로 비활성.
* **조치안**

  * `MATCH (p=...)` 경로 탐색 추가.
  * `RETURN paths` 키로 결과 통일.
  * `context_pack` 및 `debug` 필드 연동 검증 스모크 테스트 추가.

---

## **F. 테스트/CI 체계 미비**

* **확인 결과**

  * `pytest` 단위테스트 없음.
  * `integration/test_graph_migrations.py` 만 존재(시드 확인용).
  * `verify_pipeline_debug.sh` 수동 cURL 테스트만 존재.
* **영향**
  파이프라인 변경 시 리그레션 탐지 불가.
  예외/엣지 입력에 대한 보장 없음.
* **조치안**

  * pytest 기반 골든 스냅샷 테스트 추가.
  * `Hypothesis` 프로퍼티 테스트 도입 (빈 입력, UNWIND 가드 검증).
  * GitHub Actions CI 워크플로우 추가.
  * 수동 스크립트 → pytest 호출 형태로 전환.

---

# ⚙️ 종합 조치 우선순위

| 단계                    | 목표               | 주요 작업                          |
| --------------------- | ---------------- | ------------------------------ |
| 1️⃣ 스키마 정합 복원 (A ~ C) | 정합성 및 데이터 노이즈 제거 | 제약 통일, 시드 정정, 버전 필드 일원화        |
| 2️⃣ 기능 정상화 (D ~ E)    | 앙상블 + 경로 탐색 복원   | 합의 로직 및 MATCH (p=...) 경로 쿼리 추가 |
| 3️⃣ 품질 보증 (F)         | 리그레션 및 CI 체계 구축  | pytest 골든 테스트 + CI 워크플로우 도입    |

---

