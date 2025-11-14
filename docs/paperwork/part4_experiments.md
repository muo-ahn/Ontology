# 📘 **Part IV — 실험 설계 및 실험 환경 (Experiments)**

_Ontology-Grounded Vision-Language Reasoning Pipeline for Medical Imaging_
_Draft v1.0 (Based on main branch implementation)_

---

## **1. 실험 개요 (Experiment Overview)**

본 연구의 목적은 **Ontology 기반 그래프 맥락(graph context)** 이
Vision–Language–Language reasoning의 **일관성과 신뢰도**에 미치는 영향을 계량적으로 검증하는 것이다.

이를 위해 본 시스템의 세 가지 reasoning 모드 —
**V**, **VL**, **VGL** — 을 비교·평가하고,
**Graph Evidence 존재 여부**, **Slot 구조**, **Consensus 결과**를 중심으로 실험을 설계하였다.

---

## **2. 실험 목표 (Objectives)**

| 구분                             | 설명                                                           | 대응 코드                               |
| -------------------------------- | -------------------------------------------------------------- | --------------------------------------- |
| **O1. Graph Evidence 효과 검증** | 그래프 경로 유무에 따른 reasoning 변화 분석                    | `GraphContextBuilder`                   |
| **O2. V/VL/VGL 모드 비교**       | caption과 graph evidence의 상호 작용 평가                      | `pipeline.analyze()`                    |
| **O3. Consensus Core 평가**      | agreement_score와 confidence 간 상관성 분석                    | `consensus_core.py`                     |
| **O4. Slot Rebalancing 영향**    | context slot 조정이 path depth 및 degraded ratio에 미치는 영향 | `context_pack._rebalance_slot_limits()` |

---

## **3. 실험 데이터 (Dataset)**

### **3.1 구성**

실험은 다음 두 그룹의 데이터를 사용하였다.

| 데이터셋                  | 설명                                      | 비고                                            |
| ------------------------- | ----------------------------------------- | ----------------------------------------------- |
| **Seed Registry Dataset** | 시스템 시드로 등록된 Dummy Medical Images | 3개의 modality(CT, US, X-ray) 포함              |
| **C-Dataset**             | 추가 테스트용 신규 이미지                 | 기존 시드와 연결되지 않음 (alias mismatch 실험) |

총 이미지 수: 24개
평균 3.8개 Findings / Image
그래프 노드 총 125개, 관계(Relationship) 310개.

---

### **3.2 데이터 특징**

- 일부 이미지(`IMG_002`)는 modality와 caption이 불일치하도록 intentionally noise 삽입
  → graph reasoning의 노이즈 내성 평가에 사용
- 모든 데이터는 `/data/medical_dummy/images/` 경로 기준으로
  seed registry (`seed_dummy_A/B/C.cypher`)를 통해 graph로 미리 업서트됨.

---

## **4. 실험 방법 (Methods)**

---

### **4.1 Baseline vs Graph Evidence**

**목표:**
Graph Path Evidence가 reasoning 품질에 미치는 영향 측정.

| 조건                     | 설정                                                           |
| ------------------------ | -------------------------------------------------------------- |
| **Baseline (Graph OFF)** | context builder 비활성화 (`paths=[]`, `slot_rebalanced=False`) |
| **Graph ON**             | `GRAPH_PATHS_QUERY` 실행, evidence 삽입 (`paths_len>0`)        |

**측정 지표:**

- agreement_score
- path_strength
- degraded_ratio

→ 코드: `context_pack.py` 및 `pipeline.analyze()` 내부의 path_length 조건문.

---

### **4.2 V / VL / VGL 모드 비교**

**목표:**
vision caption과 graph evidence가 reasoning에 미치는 상호 효과 측정.

| 모드    | 입력 구성                      | 설명                  |
| ------- | ------------------------------ | --------------------- |
| **V**   | 텍스트 기반 LLM reasoning      | 그래프, 캡션 비활성화 |
| **VL**  | VLM caption + 텍스트 reasoning | vision 정보 추가      |
| **VGL** | 그래프 evidence + caption      | 최종 제안 모델        |

**실행:**
각 모드를 동일한 입력 이미지에 대해 3회 반복 실행,
결과를 `consensus_core`에 전달하여 비교.

→ `pipeline.analyze()` 내부 loop 실행 로그로 자동 수집.

---

### **4.3 Consensus Ablation**

**목표:**
가중치(weight) 및 anchor 설정이 최종 합의(consensus)에 미치는 영향 분석.

| 실험 설정 | anchor_mode | weight(V, VL, VGL)       | 기대 결과          |
| --------- | ----------- | ------------------------ | ------------------ |
| E1        | None        | 1.0 / 1.0 / 1.0          | baseline           |
| E2        | VGL         | 1.0 / 1.2 / 1.8          | 기본 설정          |
| E3        | VGL         | 1.0 / 1.2 / 2.0 (+bonus) | path evidence 강화 |

**평가 항목:**

- consensus.status (agree/conflict/degraded)
- confidence level
- agreement_score 변화

→ 코드: `consensus_core.compute_consensus()` 내 weight 매개변수 및 anchor_mode 제어.

---

### **4.4 Slot Rebalancing 실험**

**목표:**
findings slot이 0으로 고정되지 않도록 하는 알고리즘의 효과 검증.

| 실험 | 설정                                  | 지표                                       |
| ---- | ------------------------------------- | ------------------------------------------ |
| S1   | `_ensure_finding_slot_floor()` 비활성 | ctx_paths_len                              |
| S2   | 함수 활성화 (기본값)                  | ctx_paths_len / slot_meta / degraded_ratio |

**예상 결과:**

- S2에서 ctx_paths_len이 평균 2→4로 증가
- degraded_ratio 감소 (0.42 → 0.18)

→ 검증 코드: `tests/unit/test_context_pack.py`

---

## **5. 평가 지표 (Metrics)**

본 시스템의 평가는 모델 출력이 아닌 **구조적 지표(Structured Metrics)** 기반으로 이루어진다.
모든 지표는 `debug payload`를 통해 자동 기록된다.

---

### **5.1 Agreement Score**

[
agreement_score = \frac{|S_V \cap S_{VL} \cap S_{VGL}|}{|S_V \cup S_{VL} \cup S_{VGL}|}
]

- 각 모드의 finding slot 일치도를 정량화
- `min_agree` = 0.35 (threshold)

---

### **5.2 Path Strength**

[
path_strength = \frac{\sum_i score(p_i)}{N_{paths}}
]

- Neo4j Path 쿼리에서 segment score 평균
- Graph evidence의 품질을 나타냄
- `context_meta.path_strength` 로 기록됨.

---

### **5.3 Consensus Confidence**

| 조건                   | Confidence |
| ---------------------- | ---------- |
| agreement_score ≥ 0.75 | high       |
| 0.35 ≤ score < 0.75    | medium     |
| score < 0.35           | low        |

- `consensus.confidence` 필드로 계산
- 합의 엔진(`consensus_core.py`) 내에서 동적 결정.

---

### **5.4 Degraded Ratio**

[
degraded_ratio = \frac{N_{degraded}}{N_{total}}
]

- 합의 실패 또는 path evidence 부재로 인한 degraded 케이스 비율
- `debug.degraded = True`로 마킹된 경우를 집계.

---

### **5.5 Slot Depth Index**

[
slot_depth = \frac{context_paths_len}{slot_count}
]

- GraphContextBuilder 내 slot 재조정 효과를 평가.
- Path evidence가 많을수록 slot depth가 증가.

---

## **6. 실험 환경 (Environment)**

| 구성 요소          | 버전 / 설정                                 | 비고               |
| ------------------ | ------------------------------------------- | ------------------ |
| **OS**             | Ubuntu 22.04 / macOS 14                     | 개발·테스트 병행   |
| **Python**         | 3.10.11                                     | venv 기반          |
| **Framework**      | FastAPI + Neo4j Driver                      | REST API 구조      |
| **Database**       | Neo4j 5.x                                   | Ontology Storage   |
| **Model**          | GPT-4-turbo / LLaVA / BLIP                  | VLM + LLM 결합     |
| **Test Framework** | Pytest                                      | Unit + Integration |
| **Artifacts**      | `/artifacts/run_logs/`, `/tests/snapshots/` | 결과 자동 기록     |

---

## **7. 실행 예시 (Reproducible Evaluation)**

아래 명령으로 모든 실험을 재현할 수 있다.

```bash
# 단일 이미지 실험
curl -s -X POST "http://localhost:8000/pipeline/analyze?sync=true&debug=1" \
  -H "Content-Type: application/json" \
  -d '{
        "file_path": "/data/medical_dummy/images/api_test_data/Ultrasound-fatty-liver.png",
        "modes": ["V","VL","VGL"],
        "k": 2,
        "max_chars": 120
      }' | jq '.debug'
```

결과 예시:

```json
{
  "graph_context": { "paths_len": 3, "path_strength": 0.84 },
  "consensus": {
    "status": "agree",
    "confidence": "high",
    "agreement_score": 0.71
  },
  "degraded": false
}
```

---

## **8. 실험 설계 요약**

| 실험 코드 | 목적                     | 변수            | 주요 지표                     |
| --------- | ------------------------ | --------------- | ----------------------------- |
| **E1**    | Graph Evidence 유무 비교 | graph on/off    | path_strength, degraded_ratio |
| **E2**    | 모드 비교 (V/VL/VGL)     | caption, graph  | agreement_score               |
| **E3**    | Consensus anchor 영향    | weight, anchor  | confidence                    |
| **S1–S2** | Slot Rebalance 영향      | slot limit 설정 | ctx_paths_len, degraded_ratio |

---

## **9. 기대 효과 (Expected Outcomes)**

1. **Graph Evidence 활성화 시**

   - agreement_score 상승 (0.52 → 0.74)
   - degraded_ratio 감소 (0.40 → 0.18)

2. **VGL 모드에서**

   - confidence “high” 비율 증가
   - hallucination 유사 표현 감소

3. **Slot Rebalancing**

   - context depth 개선 (평균 path 2 → 4)
   - shallow-context 문제 해소

4. **Consensus Weight 조정**

   - path evidence가 풍부한 샘플에서 VGL anchor 효과 극대화

---

## **10. 결과 분석을 위한 데이터 기록 포맷**

모든 실험 결과는 자동으로 `/artifacts/label_drift.json` 형태로 저장되며,
각 항목은 다음 필드를 포함한다.

```json
{
  "image_id": "IMG201",
  "graph_paths_len": 3,
  "agreement_score": 0.71,
  "path_strength": 0.84,
  "consensus_confidence": "high",
  "degraded": false,
  "anchor_mode": "VGL"
}
```

이 데이터는 Part V의 결과 분석에서 통계 및 시각화에 직접 활용된다.
