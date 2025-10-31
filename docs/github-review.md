# GitHub Review (2025-10-31)

## Overview
- 리뷰 범위: `grounded-ai` 서비스 계층, 파이프라인 라우터, 시드/도커 스크립트, 유사성 계산 모듈.
- 최신 로그와 main 브랜치 소스를 대조해 확정된 8가지 이슈를 검증하고 근거 라인을 확보했다.

## 확정 이슈 검증
- **경로 다양성 부족 → `ctx_paths_len` 정체**  
  - Top-k 쿼리가 `SIMILAR_TO` 기반 단일 패턴만 수집해 경로 구성이 1~2개로 제한됨 (`grounded-ai/api/services/graph_repo.py:131`).  
  - 컨텍스트 빌더는 동일 경로를 반복적으로 줄이는 루프만 있으므로 k 값을 늘려도 구조가 바뀌지 않는다 (`grounded-ai/api/services/context_pack.py:122`).

- **이미지 업서트 식별 불안정**  
  - 파이프라인이 VLM이 만든 ID가 있어도 파일 경로에서 파생한 ID로 강제로 덮어써 긴 `ULTRASOUND…` 식별자가 생성된다 (`grounded-ai/api/routers/pipeline.py:546-557`).  
  - 업서트 쿼리는 `storage_uri` 로 찾은 기존 노드가 없으면 새 `image_id` 로 다시 MERGE 하므로 동일 이미지라도 URI 변형에 따라 분리 노드가 생길 수 있다 (`grounded-ai/api/services/graph_repo.py:34-87`).

- **합의 로직이 상시 disagree/low 로 귀결**  
  - 기본 합의 임계치가 0.6이고 2개 모드만 일치해도 가중치가 1.0 이하면 fallback 도 거부돼 `status` 가 쉽게 "disagree" 로 떨어진다 (`grounded-ai/api/routers/pipeline.py:121-214`).  
  - `presented_text` 는 불일치 시 `"??? 확신:"` 접두어를 붙여 노이즈를 키운다 (`grounded-ai/api/routers/pipeline.py:292-307`).

- **언어/형식 혼선**  
  - V/VGL 템플릿이 다국어와 잘림 문자(�)가 섞여 프롬프트 자체에서 혼란을 유발한다 (`grounded-ai/api/routers/llm.py:61-73`).  
  - 컨텍스트 출력도 동일한 깨진 문자열을 반환해 모델 입력 품질을 떨어뜨린다 (`grounded-ai/api/services/context_pack.py:18-28`).

- **Top-k 민감도 저하 및 패턴 편향**  
  - `collect(...)[0..$k]` 로 잘라내기만 하고 패턴이 단일해 k 값 증분이 실효가 없다 (`grounded-ai/api/services/graph_repo.py:166-167`).  
  - `build_bundle` 가 k 감소 재시도 외 다른 패턴을 시도하지 않아 경로 유형이 고정된다 (`grounded-ai/api/services/context_pack.py:122-137`).

- **SIMILAR_TO 컷/이웃 수 제한**  
  - 유사도 기본 임계값이 0.5, top_k=10 으로 고정돼 고비용 실험에서 후보가 거의 남지 않는다 (`grounded-ai/api/services/similarity.py:29-72`).  
  - 파이프라인은 임계값을 요청 파라미터로 낮출 수 있지만 기본값이 logs 대비 지나치게 높다 (`grounded-ai/api/routers/pipeline.py:505-511`).

- **요약 SUMMARY 편향**  
  - 번들 요약은 `HAS_FINDING` / `DESCRIBED_BY` 두 관계만 수집해 다른 패턴 집계가 불가능하다 (`grounded-ai/api/services/graph_repo.py:90-128`).  
  - `context_pack` 은 위 요약을 그대로 출력하므로 LOCATED_IN, RELATED_TO 등의 지표가 전혀 노출되지 않는다 (`grounded-ai/api/services/context_pack.py:103-155`).

- **환경/도구 불일치**  
  - 평가 스크립트는 cypher-shell 로컬 탐색 후 도커 exec 로 폴백하지만 환경 변수 안내가 부족해 `command not found` 를 자주 유발한다 (`grounded-ai/scripts/run_eval_dummy.sh:18-83`).  
  - Compose 파일은 더 이상 권장되지 않는 `version` 키를 사용하고 있다 (`grounded-ai/docker-compose.yml:1-4`).

## 추가 관찰
- 컨텍스트 요약/경로 문자열이 UTF-8 표현을 그대로 포함하고 있으나 Windows 기본 인코딩에서 깨져 로그 분석이 어렵다 (`grounded-ai/api/services/context_pack.py:18-29`).
- 테스트 스위트는 통합 테스트 1개 뿐이며 pytest 골든 케이스나 CI 워크플로가 정의돼 있지 않다 (`tests/test_paths_and_analyze.py:1`).

## 권장 후속 조치 초안
- 경로 쿼리를 UNION 기반 다중 패턴 + dedup 구조로 리팩터링하고 패턴별 k, sim_cut 파라미터를 노출한다.
- 이미지 업서트는 `storage_uri` → `image_id` 로 1차 MERGE 후 `image_id` 를 재사용하도록 강제한다.
- 합의 로직에 2/3 투표, 모드 가중치, 불일치 시 확신 레벨 하향 규칙을 재설계하고 출력 언어를 한국어로 고정한다.
- SUMMARY/FACTS에 LOCATED_IN, RELATED_TO, SIMILAR_TO 집계를 추가해 증거 다변화를 확보한다.
- Docker/스크립트 환경 메시지를 개선하고 Compose 구문을 최신 스펙으로 갱신한다.

