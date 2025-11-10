# Pipeline Modes & Consensus Policy

Vision(V), Vision+Language(VL), Vision+Graph+Language(VGL) 세 모드의 입력, 증거 소스, LLM 프롬프트 구성,
가중치, 폴백 전략을 정의한다.

---

## 1. Mode Matrix

| Mode | Input Source | Evidence | LLM Prompt Context | Weight |
| --- | --- | --- | --- | --- |
| `V` | Vision encoder caption | None | Caption only | 0.50 |
| `VL` | Caption + extracted report text | Findings list | Caption + findings table | 0.75 |
| `VGL` | Caption + GraphBundle | Graph paths + facts | Caption + findings + path snippets | 1.00 (+0.1 graph bonus) |

- Weight 는 합의 계산 시 기본 점수이며, `consensus.compute_consensus` 에서 필요에 따라 정규화된다.
- `graph bonus` 는 paths 가 비어있지 않을 때 VGL 모드 가중치에 추가된다.

---

## 2. Prompt Templates

| Mode | Template 핵심 요소 |
| --- | --- |
| `V` | **Instruction:** “Describe clinical impressions based solely on the caption.”<br>**Input:** caption text.<br>**Guard:** “If unsure, say you are unsure.” |
| `VL` | **Instruction:** “Combine image caption and extracted report findings.”<br>**Input:** caption + findings JSON.<br>**Guard:** highlight disagreements between caption/report. |
| `VGL` | **Instruction:** “Use graph facts and paths as primary evidence.”<br>**Input:** caption + findings + path triples summarised.<br>**Guard:** include `context_paths_len` count. |

모든 템플릿은 `ModePrompt` 모델로 파라미터화하며, 실험 시 JSON 스냅샷을 남긴다.

---

## 3. Execution Policy

1. 세 모드를 병렬 실행 가능하나, VGL 은 GraphBundle 준비 완료 이후 시작.
2. 개별 모드 타임아웃 기본 12s, 실패 시 `ModeResult.error` 기록 후 합의에서 제외.
3. Vision encoder rate-limit 을 피하기 위해 동일 이미지 ID 는 5분 동안 캐시.

---

## 4. Consensus Rules

| 상황 | 처리 |
| --- | --- |
| 3 모드 가용 | 2/3 이상 동일 finding → `confidence=high` |
| 2 모드 가용 | 가중치 평균 ≥ 0.7 → `confidence=medium`, 미만 → `low` |
| 1 모드 가용 | `confidence=low`, 응답에 “Single-mode output” 프리픽스 |
| 텍스트 불일치 | agreement_score < 0.4 → “Low confidence” prefix |
| Finding 충돌 | 서로 다른 modality 주장 → `debug.modes[].conflict=true` 및 경고 문구 |

---

## 5. Telemetry & Metrics

- `mode.v.latency_ms`, `mode.vl.latency_ms`, `mode.vgl.latency_ms`
- `consensus.agreement_score`, `consensus.mode_weights`
- `mode.failures{mode=<V/VL/VGL>,reason=<timeout/error>}`

---

## 6. Future Extensions

1. **Adaptive Weighting:** 모드별 precision/recall 데이터를 기반으로 학습된 weight 를 로드.
2. **Specialty Modes:** 예: `VCT` (CT 특화) with custom prompt and graph filters.
3. **Human-in-the-loop:** agreement_score < 0.3 인 케이스를 검토 큐로 push.

이 문서는 `module_specs.md` 의 `ConsensusCore` 구현과 `testing_strategy.md` 의 스냅샷 테스트 시나리오의 근거가 된다.
