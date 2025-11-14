# Spec-03 컨텍스트 소스 일원화 검증 메모

## 1. 개요
- 참조 문서: `docs/stabilization/spec.md` 중 **Spec-03 컨텍스트 일원화**(lines 176~245).
- 검증 일시: 2025-02-15 (vision pipeline debug 스크립트 실행 기준).
- 검증 명령: `./scripts/vision_pipeline_debug.sh "<이미지 경로>" '{"force_dummy_fallback": true}'`.
- 점검 목적: `context_paths`, `facts`, `triples summary`가 동일 Neo4j 쿼리 기반인지 확인하고 Spec-03 준수 여부 기록.

## 2. 검사 대상 로그 샘플
| 케이스 | 이미지 ID | 모달리티 | 특이사항 |
| --- | --- | --- | --- |
| A | `IMG_001` | XR | fallback-only, findings 없음 |
| B | `IMG_003` | CT | `FIND-001` 1건 존재 |
| C | `IMG201` | US | mock_seed로 `F201`, `F202` 제공 |

## 3. Spec 요구사항 대비 관찰
1. **단일 그래프 소스 사용 (`context_paths`, `facts`, `triples`)**  
   - 모든 케이스에서 `graph_context.paths`가 `[]`인 상황과 `graph_context.triples`의 `[EVIDENCE PATHS]\nNo path generated (0/2)` 문자열이 일치.  
   - in-memory 경로나 별도 요약이 삽입된 정황 없음 → Spec-03 1차 요구사항 충족.

2. **facts JSON vs `context_findings_head` 일치 (`docs/stabilization/spec.md:232`)**  
   - IMG_001: 두 필드 모두 빈 목록.  
   - IMG_003: `FIND-001` 레코드가 두 위치에 동일하게 노출.  
   - IMG201: `F201`, `F202`가 순서만 다를 뿐 동일 데이터로 존재.  
   - 결과적으로 diff 불일치 없음 → 요구 충족.

3. **`paths_len == 0` 시 triples summary에 명시적 안내 (`No path generated`)**  
   - 세 케이스 모두 `paths` 길이가 0일 때 요약 문자열에 “No path generated (0/2)” 문구가 포함됨.  
   - spec 검증표 2항 충족.

4. **`context_consistency` 자동 점검 및 에러 리포트**  
   - Debug payload의 `context_consistency=true`, `context_consistency_reason` 미기록.  
   - `errors` 배열에 `{"stage":"context","msg":"facts_paths_mismatch"}`가 추가되지 않은 것으로 보아 내부 self-check 통과.  
   - Spec-03의 디버그 요구사항 충족.

## 4. 미충족 / 미노출 항목
| 항목 | 요구사항 | 관찰 | 조치 필요 여부 |
| --- | --- | --- | --- |
| `graph_context.fallback_reason` | ContextOrchestrator 개선 플랜 2번: 그래프 미반환 시 이유 표준 필드 확보 | 현재 payload에서 해당 필드 확인 불가 | ✅ (추가 구현 및 노출 필요) |

## 5. 권장 후속 조치
1. `/pipeline/analyze` 결과 구조에 `graph_context.fallback_reason` 또는 동등한 플래그를 포함시키고, 그래프 미확보 시 `"no_graph_paths"` 등을 셋팅.
2. Spec-03용 pytest(`tests/test_context_orchestrator.py` 등)에 위 플래그 검증 로직을 추가하여 CI에서 자동 감시.
3. `vision_pipeline_debug.sh` 출력 파서(jq)에도 해당 필드를 노출시켜 운영자가 즉시 원인을 식별할 수 있도록 보강.

## 6. 결론
- Spec-03 핵심 요구사항(단일 그래프 소스, facts/paths/summary 일치, fallback 표기, 디버그 self-check)은 현재 출력 기준으로 충족됨.
- 단, fallback 이유를 구조화된 필드로 노출하는 보강 항목이 아직 미완료 상태로 판단되므로, 조치 #1~#3을 마무리해야 최종 완료로 간주 가능.
