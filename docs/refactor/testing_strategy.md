# Testing & CI Strategy

리팩터 완료 후 파이프라인 품질을 보장하기 위한 테스트 종류, 샘플 데이터, CI 파이프라인을 정의한다.

---

## 1. Test Pyramid

| 레벨 | 파일 | 목적 | 요구 사항 |
| --- | --- | --- | --- |
| Unit | `tests/test_image_identity.py` | 파일명 파싱, seed hit, slug fallback, 502 예외 가드(S08) | 파라미터화된 입력/출력 |
| Unit | `tests/test_context_orchestrator.py` | slot rebalance, dedup | Neo4j mock |
| Unit | `tests/test_consensus.py` | 가중치, 불일치 경고 | fixtures/ModeResult |
| Snapshot | `tests/test_consensus_snapshot.py` | IMG201 케이스 agreement score 잠금 | JSON 골든 파일 |
| Integration | `tests/integration/test_pipeline_e2e.py` | dummy 이미지 → full response | test client + mocked LLM |
| Migration | `tests/integration/test_graph_migrations.py` | seed.cypher idempotency | Neo4j test container |

---

## 2. Fixtures & Golden Data

- `tests/fixtures/dummy_image_IMG201.json`: 이미지 메타 + expected modality.
- `tests/fixtures/graph_bundle_IMG201.json`: GraphBundle snapshot.
- `tests/fixtures/mode_results_IMG201.json`: 각 모드 텍스트/찾은 소견.
- Golden 파일은 `tests/golden/*.json` 에 저장하며, 업데이트 시 `pytest --update-golden` 플래그 사용.

---

## 3. CI Workflow (GitHub Actions)

1. **Install step:** `pip install -r requirements.txt` + `poetry install` (선택).
2. **Static checks:** `ruff check`, `mypy`.
3. **Unit tests:** `pytest tests -m "not integration"`.
4. **Integration (nightly):** 사용량 절감을 위해 스케줄 기반으로만 Neo4j/LLM mock 포함 실행.
5. **Artifact upload:** 실패 시 `artifacts/debug_payload/*.json` 첨부로 재현성 보장.

---

## 4. Regression Guardrails

- 합의 점수가 0.1 이상 변하면 테스트 실패.
- `GraphBundle.paths` 길이가 0이면 `test_context_orchestrator` 경고를 발생시켜 Issue E 회귀 방지.
- `identify_image()` 가 registry miss 시에도 slug fallback 으로 `IMG_<SLUG>_<CRC>` 를 생성하며, 최종 ID 가 없으면 반드시 `ImageIdentityError(code="unresolved_image_id")` 를 발생시키는지 확인 (Spec S08).
- `ImageIdentity.seed_hit` 비율이 0.8 미만이면 실패(시드 데이터가 깨졌는지 확인).

---

## 5. Local Developer Workflow

```bash
export NEO4J_URI=bolt://localhost:7687
export NEO4J_USER=neo4j
export NEO4J_PASSWORD=test
poetry run pytest --maxfail=1 --ff
```

- `scripts/verify_pipeline_debug.sh` 는 pytest wrapper 로 대체하고, 결과를 `docs/VisionPipelineDebug/` 에 첨부한다.
- pre-commit 훅으로 `ruff`, `mypy`, `pytest -m "unit"` 실행 설정.

---

## 6. Open Questions

1. V/VL/VGL 각각에 대한 synthetic fixture 를 자동 생성할지 여부.
2. CI 에서 Neo4j container 기동 시간을 단축하기 위한 seed snapshot 전략.
3. Artifact Registry(JSONL) 도입 시 테스트 간 충돌 방지 설계.

---

이 전략은 Issue F(테스트/CI 미비)를 해결하기 위한 최소 기준이다. 골든 스냅샷이 업데이트될 때마다 PR 설명에
`why`/`evidence` 를 포함해 장기 유지보수를 용이하게 한다.
