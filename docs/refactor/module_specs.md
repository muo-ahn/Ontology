# Module Specification Catalog

파이프라인을 구성하는 서비스/모델 모듈의 입력·출력·사이드 이펙트를 표준화한다. 모든 함수는 동기 서명을 유지하지만
I/O 추상화를 통해 추후 async 전환을 지원한다.

---

## 1. `services/image_identity.py`

| 항목 | 세부 내용 |
| --- | --- |
| Public API | `def identify_image(file_path: str, modality: Optional[str]) -> ImageIdentity` |
| Output Model | `ImageIdentity(id: str, storage_uri: str, seed_hit: bool, registry_source: Optional[str])` |
| Steps | (1) 파일명 슬러그화 → IMG### 추출 → (2) `seed_registry.json` 조회 → (3) 스토리지 경로 생성 → (4) fallback 해시 |
| Validation | `id` 는 `[A-Z0-9_]+`, `storage_uri` 는 `s3://` 또는 `gs://` prefix |
| Fallback | Normalizer/registry miss 시 `IMG_<SLUG>_<CRC>` 규칙으로 deterministic slug 재구성 (Graph Schema Issue A 제약을 준수) |
| Errors | 잘못된 파일명: `ImageIdentityError(code="invalid_filename")`; slug 생성 실패 또는 최종 `image_id` 미생성 시 `ImageIdentityError(code="unresolved_image_id", status_code=502)` (Spec S08) |

> **S08 Guardrail:** `identify_image()` 는 내부적으로 `normalized_image_id` 변수를 초기화한 뒤 모든 파생 로직에서 이 값을 갱신해야 하며, 마지막까지 비어 있다면 FastAPI 계층에 502 오류를 전달할 수 있도록 예외를 던진다. NameError/stack trace 는 노출되지 않는다.

---

## 2. `services/context_orchestrator.py`

| 항목 | 세부 내용 |
| --- | --- |
| Public API | `class ContextOrchestrator:` with `build_bundle(image: ImageIdentity, limits: ContextLimits) -> GraphBundle` |
| Dependencies | `GraphRepo`, `GraphContextBuilder`, `GraphPackBuilder` |
| ContextLimits | `k_findings`, `k_reports`, `k_similarity`, `max_paths` |
| Algorithm | slot rebalance → findings 우선 확보 → report summary 증강 → similarity paths UNWIND |
| Output Guarantees | `GraphBundle.summary/facts/paths` 길이는 각 limit 이하, dedup 적용 |
| Telemetry | `context.debug.slot_allocation`, `graph_query.cypher` 이벤트 기록 |

---

## 3. `services/consensus.py`

| 항목 | 세부 내용 |
| --- | --- |
| Public API | `def compute_consensus(modes: list[ModeResult]) -> ConsensusResult` |
| ModeResult | `mode: Literal["V","VL","VGL"]`, `text`, `findings`, `latency_ms`, `evidence_refs` |
| Weights | 기본 `{V:0.5, VL:0.75, VGL:1.0}` + 그래프 bonus (`+0.1`) when `context.paths` hit |
| Agreement | `agreement_score = jaccard(text_tokens) * overlap(findings)` |
| Policies | (a) 2/3 majority → high confidence, (b) 모드간 충돌 시 “Low confidence” prefix |
| Outputs | `ConsensusResult.text`, `agreement_score`, `mode_weights`, `confidence_label`, `conflict_modes`, `agreement_components` |

---

## 4. `services/debug_payload.py`

| 항목 | 세부 내용 |
| --- | --- |
| Public API | `class DebugPayloadBuilder:` with `set_stage`, `record_identity`, `record_context`, `record_consensus`, `payload()` |
| DebugInputs | image identity snapshot, fallback 메타, context bundle stats, per-mode diagnostics, evaluation payload |
| Required Keys | `context_slot_limits`, `finding_fallback`, `seeded_finding_ids`, `context_paths_len`, `consensus` |
| Optional Keys | `graph_degraded`, `graph_paths_strength`, `similar_seed_images`, `evaluation` |
| Usage | Router가 builder 메서드로 이벤트를 기록한 뒤 `payload()`를 응답 `debug` 필드로 사용 |

---

## 5. `services/healthcheck.py`

| 항목 | 세부 내용 |
| --- | --- |
| Purpose | `/healthz` 라우터에서 LLM, Vision encoder, Neo4j readiness 를 보고 |
| Checks | (1) Vision ping, (2) LLM short completion, (3) `GraphRepo.run("RETURN 1")` |
| Output | `HealthReport(status: Literal["ok","degraded","down"], components: dict)` |

---

## 6. Shared Pydantic Models (`graph/models.py`)

| 모델 | 필수 필드 | 비고 |
| --- | --- | --- |
| `Finding` | `id`, `type` | `confidence` 0~1, `location` optional |
| `ImageNode` | `id`, `storage_uri` | `modality` optional |
| `ReportNode` | `id`, `summary`, `findings: List[Finding]` | summary 는 512 tokens 제한 |
| `PathRow` | `slot`, `triples`, `score` | triples 는 `"Node-[:REL]->Node"` 문자열 |
| `GraphBundle` | `summary`, `facts`, `paths` | 기본 빈 리스트 |
| `ModeResult` | `mode`, `text`, `findings`, `latency_ms`, `metadata` | metadata 에 증거 경로 포함 |
| `ConsensusResult` | `text`, `agreement_score`, `mode_weights`, `confidence_label` | label 은 `high/medium/low` |

---

## 7. Non-Functional Requirements

- **Determinism:** 동일 입력은 동일 컨텍스트 및 합의 결과를 생성해야 하며, 랜덤 요소는 seed 로 제어한다.
- **Logging:** 모든 모듈은 `structlog` 컨텍스트를 받아 trace id 를 추가해야 한다.
- **Extensibility:** 새로운 모드를 추가할 때 `ModeRegistry` 에 등록하고 Pydantic `Literal` 업데이트 필요.

이 문서는 `architecture.md` 와 함께 읽으며, 구체적 데이터 스키마는 `graph_schema.md`, 모드 상세는
`pipeline_modes.md`, 테스트 및 마이그레이션은 각 전용 문서를 참고한다.
