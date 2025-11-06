# 테스트 스위트 개요

`tests/` 디렉터리에 포함된 주요 pytest 스크립트와 커버 범위, 의존성, 실행 팁을 정리한다.  
언급되지 않은 스크립트가 추가되면 이 문서를 함께 업데이트해 테스트 범위를 추적하자.

## 1. 요약 표

| 파일 | 커버 범위 | 외부 의존성 | 실행 메모 |
| ---- | --------- | ----------- | --------- |
| `tests/test_context_rebalance.py` | 컨텍스트 슬롯 리밸런싱(`_rebalance_slot_limits`)과 `_replace_image_tokens` | 없음 (Neo4j/py2neo 스텁 주입) | 순수 단위 테스트. 슬롯 재분배와 토큰 치환이 기대 값과 일치하는지 검증한다. |
| `tests/test_dummy_registry.py` | `DummyImageRegistry` 및 `DummyFindingRegistry` | `data/medical_dummy/*.csv` | 더미 레지스트리의 ID/별칭/경로 매핑과 fallback finding CSV를 검증. CSV가 없으면 실패한다. |
| `tests/test_normalizer.py` | `services.normalizer` 의 fallback 및 VLM 정규화 경로 | 없음 | `DummyVLMRunner` 스텁 사용. 레지스트리 기반/키워드 기반/강제 fallback 시나리오를 async 테스트로 확인한다. |
| `tests/test_paths_and_analyze.py` | `/pipeline/analyze` 흐름 및 그래프 컨텍스트 관련 통합 시나리오 | 부분적으로 Neo4j + `cypher-shell` (스킵 가능) | FastAPI 라우터를 통해 파이프라인을 호출한다. 일부 테스트는 Neo4j 시드 픽스처에 의존하고, 나머지는 내부 스텁(_PipelineHarness)으로 동작한다. |

## 2. 세부 설명

### `test_context_rebalance.py`
- `_rebalance_slot_limits` 가 비어 있는 findings 슬롯을 리포트로 재할당하는지, 결과 슬롯 배분을 유지하는지 등을 검증한다.
- `_replace_image_tokens` 로 `(IMAGE_ID)` 플레이스홀더가 최종 텍스트에서 치환되는지 확인한다.
- 외부 서비스 의존이 없어 CI에서 가장 빠르게 수행되는 단위 테스트다.

### `test_dummy_registry.py`
- `DummyImageRegistry.resolve_by_id/resolve_by_path` 가 시드 CSV(`imaging.csv`)의 저장 경로와 일치하는지 연속 테스트한다.
- `DummyFindingRegistry.resolve` 가 fallback finding CSV(`fallback_findings.csv`)와 일치하는 결과를 반환하는지 검증한다.
- 테스트 실행 전 `data/medical_dummy/` 경로가 최신 시드 파일을 포함해야 한다.

### `test_normalizer.py`
- `normalize_from_vlm` 이 레지스트리 기반 fallback, 키워드 기반 fallback, 강제 fallback 을 어떻게 처리하는지 async 테스트로 검증한다.
- 외부 API 호출 대신 `DummyVLMRunner` 를 사용하므로 추가 의존성 없이 실행된다.

### `test_paths_and_analyze.py`
- FastAPI 라우터를 통해 `/pipeline/analyze` 를 호출하며 다음을 검증한다.
  - 그래프 근거가 없을 때 `status="low_confidence"` 로 다운그레이드 되는지.
  - V/VL 결과가 그래프와 충돌하더라도 VGL이 합의를 주도하는지.
  - 더미 lookup 시 `storage_uri` 가 레지스트리 경로와 일치하는지.
  - 리밸런싱된 컨텍스트에 `DESCRIBED_BY` 경로가 포함되는지.
  - `k_reports` 오버라이드가 자동 리밸런싱과 동일한 컨텍스트를 반환하는지.
- 파일 하단의 `ensure_dummy_c_seed` 픽스처는 `@pytest.mark.usefixtures` 로 연결된 테스트에만 적용된다.
  - Neo4j 환경이 준비되어 있으면 시드(`seed_dummy_C.cypher`)를 로드한 뒤 통합 테스트를 실행한다.
  - Neo4j를 사용할 수 없으면 `NEO4J_SKIP=1` 또는 `cypher-shell` 부재로 해당 테스트가 스킵된다.
  - 스텁 기반 테스트(`_PipelineHarness` 사용)는 Neo4j 없이 항상 실행된다.

## 3. 실행 방법

```bash
# 전체 테스트
python -m pytest

# 통합 테스트를 스킵하고 빠르게 확인하고 싶을 때
NEO4J_SKIP=1 python -m pytest

# 특정 모듈만 실행
python -m pytest tests/test_dummy_registry.py

# 특정 테스트만 실행
python -m pytest tests/test_paths_and_analyze.py::test_pipeline_report_override_parity_matches_auto
```

## 4. 주의 사항

- Neo4j 의존 테스트는 `cypher-shell` 바이너리와 접속 가능한 Neo4j 인스턴스를 요구한다. CI 환경에서는 미리 설치하거나 `NEO4J_SKIP=1` 로 스킵하도록 설정한다.
- CSV 기반 테스트는 `data/medical_dummy/` 아래의 시드 파일이 최신 상태여야 통과한다.
- 새로운 테스트를 추가하면 이 문서에 범위/의존성/주의 사항을 함께 기록해 유지보수성을 높이자.
