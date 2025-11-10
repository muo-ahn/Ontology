# Refactor Migration Checklist

리팩터 구현을 단계적으로 진행하기 위한 체크리스트. 각 항목은 완료 조건과 검증 방법을 포함한다.

---

## Legend

- ✅ 완료
- 🚧 진행 중
- ☐ 미착수

---

## 1. Schema & Data Integrity (Issues A–C)

| 상태 | 작업 | 완료 조건 | 검증 |
| --- | --- | --- | --- |
| ✅ | Image 제약 통일 | `seed.cypher` 가 `MERGE (img {image_id:…})` 사용, constraint `img.image_id` | `pytest tests/integration/test_graph_migrations.py` |
| ✅ | 모달리티/캡션 정합성 | Seed 데이터 검토 및 IMG_002 수정 | `rg "IMG_002" seed.cypher` + reviewer 확인 |
| ✅ | version 필드 통일 | `AIInference.version_id` 로 필드명 변경, Neo4j data migrate | Cypher migration + unit tests |

---

## 2. Module Extraction

| 상태 | 작업 | 완료 조건 | 검증 |
| --- | --- | --- | --- |
| ☐ | `image_identity.py` 생성 | 서비스/테스트/DI wiring 완료 | `pytest tests/test_image_identity.py` |
| ☐ | `context_orchestrator.py` 생성 | GraphBundle Typed 반환 | `pytest tests/test_context_orchestrator.py` |
| ☐ | `consensus.py` + 모드 합의 | 가중치/투표 로직 구현, snapshot test 추가 | `pytest tests/test_consensus_snapshot.py` |
| ☐ | `debug_payload.py` | 디버그 JSON 스키마 문서화 | Response schema diff |

---

## 3. Documentation

| 상태 | 작업 | 완료 조건 | 검증 |
| --- | --- | --- | --- |
| 🚧 | docs/refactor/* 작성 | `architecture`, `module_specs`, `graph_schema`, `pipeline_modes`, `testing_strategy` | Docs lint/확인 |
| ☐ | README 업데이트 | Disclaimer, 시스템 다이어그램, spec 링크 | `markdownlint README.md` |

---

## 4. Testing & CI

| 상태 | 작업 | 완료 조건 | 검증 |
| --- | --- | --- | --- |
| ☐ | pytest 스냅샷 infra | `--update-golden` 플래그 구현 | Snapshot tests |
| ☐ | GitHub Actions Workflow | lint + unit + nightly integration | Workflow run |
| ☐ | Seed regression guard | nightly job + alert | CI logs |

---

## 5. Deployment & Ops

| 상태 | 작업 | 완료 조건 | 검증 |
| --- | --- | --- | --- |
| ☐ | Healthcheck 모듈화 | `/healthz` 가 LLM/Vision/Neo4j 상태 리턴 | curl healthz |
| ☐ | Debug artifact 저장 | `artifacts/debug_payload/*.json` 업로드 | CI artifact |
| ☐ | Telemetry 필드 표준화 | `trace_id`, `image_id`, `mode` 필수 포함 | 로그 샘플 |

---

### How to Use

1. 각 PR 은 해당 체크리스트 항목을 참조하고, 완료 시 README 혹은 docs 의 링크를 포함한다.
2. 릴리즈 플랜 문서(TicketPlan.md)에서 이 체크리스트의 진행률을 인용한다.
3. 완료 시점에는 `docs/refactor/spec_refactor_plan.md` 를 최신 상태/링크로 업데이트한다.
