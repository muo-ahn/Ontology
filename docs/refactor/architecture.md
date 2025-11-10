# Ontology Pipeline Architecture Specification

이 문서는 `/pipeline/analyze` 엔드포인트 뒤에서 동작하는 Vision→Graph→LLM 파이프라인의 구조와 책임을
명확히 정의한다. 목표는 모듈 단위 확장, 명세 기반 테스트, 연구 재현성이다.

---

## 1. Scope & Guarantees

| 항목 | 설명 |
| --- | --- |
| 단일 진입점 | FastAPI 라우터 `grounded_ai.api.routers.pipeline.analyze` |
| 비즈니스 목표 | 의료 영상 입력을 그래프 컨텍스트와 LLM 응답으로 변환 |
| 품질 특성 | 재현 가능한 결과(JSON 스냅샷), 명시적 실패 응답, 디버그 데이터 보존 |
| 외부 의존성 | Vision Encoder, Neo4j, LLM/VLM providers, Object Storage |

---

## 2. Layered View

```
┌─────────────┐    ┌────────────────┐    ┌────────────────┐    ┌────────────────┐    ┌────────────────┐
│ HTTP Router │ →  │ Request Model  │ →  │ Image Identity  │ →  │ Context Builder │ →  │ Mode Executors │
└─────────────┘    └────────────────┘    └────────────────┘    └────────────────┘    └────────────────┘
                                                                                              │
                                                                                              ↓
                                                                       ┌────────────────┐  ┌───────────────┐
                                                                       │ Consensus Core │→│ Debug Payload │
                                                                       └────────────────┘  └───────────────┘
                                                                                            │
                                                                                            ↓
                                                                                       JSON Response
```

각 레이어는 Pydantic 모델 계약을 통해 연결된다. 상위 레이어는 하위 내부 상태에 접근하지 않고, 모듈 경계를
통해 의존성을 주입받는다.

---

## 3. Request Lifecycle

| 단계 | 모듈 | 입력 → 출력 | 실패 전략 |
| --- | --- | --- | --- |
| 1. 요청 파싱 | `RequestSchema` | FastAPI Request → `PipelineRequest` | 유효성 오류 시 422 + 필드 메시지 |
| 2. 이미지 식별 | `services.image_identity.identify_image` | `PipelineRequest` → `ImageIdentity` | seed miss 시 `seed_hit=False` 로 표기, 하위 단계 진행 |
| 3. 컨텍스트 구축 | `services.context_orchestrator.ContextOrchestrator` | `ImageIdentity` + 파라미터 → `GraphBundle` | 그래프 미연결 시 빈 summary/facts, paths fallback |
| 4. 모드 실행 | `services.modes.run_mode(*)` | Vision/VL/VGL 입력 → `ModeResult` | 실패 모드만 제외, 나머지 계속 |
| 5. 합의 | `services.consensus.compute_consensus` | `List[ModeResult]` → `ConsensusResult` | 입력 모드 < 1 시 503 에러 |
| 6. 디버그 패키징 | `services.debug_payload.assemble` | 중간 산출물 | 항상 실행, 데이터가 없는 필드는 생략 |
| 7. 응답 포맷 | `PipelineResponse` | 합의 결과 + 디버그 | OpenAPI 스키마 준수 |

---

## 4. Component Responsibilities

| 컴포넌트 | 책임 | 비고 |
| --- | --- | --- |
| Router (`pipeline.py`) | 순서 정의, DI, 오류 매핑 | 비즈니스 로직 없음 |
| `ImageIdentityService` | 파일명/메타에서 ID·스토리지 URI·seed hit 결정 | Neo4j 조회 없이 실행 |
| `ContextOrchestrator` | GraphRepo 호출, slot rebalance, dedup | GraphBundle Typed 반환 |
| Mode Executors | Vision, VL, VGL 별 캡션/그래프 증거 수집 | 레이트리밋/타임아웃 처리 포함 |
| `ConsensusCore` | 가중치 계산, 불일치 감지, 확신도 산출 | 2/3 majority + weighted score |
| `DebugPayloadBuilder` | 실험 재현용 메타데이터, path preview 저장 | JSON serializable |
| `HealthcheckService` | 외부 의존성 readiness 확인 | 독립 라우터에서 호출 |

---

## 5. Data Contracts

- 모든 내부 데이터 교환은 `graph/models.py` 및 `api/schema.py`에 정의된 Pydantic 모델을 사용한다.
- `GraphBundle.paths`는 최소 `slot`, `triples`, `score` 세 필드를 가져야 하며, 빈 리스트가 기본.
- 모드 결과(`ModeResult`)는 `text`, `findings`, `mode`, `latency_ms`, `metadata` 필드를 포함한다.
- 합의 결과(`ConsensusResult`)는 `agreement_score ∈ [0,1]` 와 `mode_weights` 맵을 포함한다.

---

## 6. Error & Observability

| 유형 | 처리 | 노트 |
| --- | --- | --- |
| Upstream 4xx | FastAPI 자동 처리, 메시지 그대로 전달 | 입력 유효성 확보 |
| Vision/LLM 실패 | 개별 모드만 제외, `debug.modes[mode].error` 로 기록 | 최소 1 모드 성공 요구 |
| GraphRepo 오류 | 재시도 1회, 실패 시 503 + `graph_unavailable=true` | Neo4j 타임아웃 대비 |
| Consensus 실패 | 모드 없음 → 503, 점수 계산 불가 → 500 | Sentry 태깅 |
| Observability | 구조화 로그 + OpenTelemetry trace + debug payload | trace id 를 응답에 반영 |

---

## 7. Deployment Considerations

- 모든 서비스 모듈은 순수 Python 함수이므로 동기 테스트가 가능하지만, I/O 의존성은 `GraphRepo`/LLM 클라이언트에서
  비동기화 계획(Section IX)과 호환되도록 인터페이스를 좁게 유지한다.
- 환경 변수는 `GROUNDING_*` prefix 로 통일하고, FastAPI start-up 시 스키마 검증을 수행한다.
- README “Non-production Disclaimer”와 시스템 다이어그램은 이 아키텍처를 기준으로 업데이트한다.

---

이 명세는 docs/refactor 디렉터리에 위치한 다른 세부 스펙(`module_specs.md`, `graph_schema.md`, `pipeline_modes.md`,
`testing_strategy.md`, `migration_checklist.md`)과 함께 사용되어야 한다.
