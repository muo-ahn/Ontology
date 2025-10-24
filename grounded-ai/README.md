# Ontology + vLM + LLM Local Prototype

## Overview
- Prototype playground for experimenting with a hybrid Ontology + vLM + LLM stack purely on a local workstation.
- Target hardware: RTX 4070 Laptop GPU and Apple Silicon (M4) with Metal acceleration.
- Goal: validate the end-to-end orchestration flow (Streamlit UI → FastAPI → vLM → LLM → Neo4j/Qdrant) against a controlled medical dummy dataset.

## TL;DR
| Mode | Context Source | What it shows |
|------|----------------|---------------|
| **V**   | Vision caption only | Fast preview of the raw vLM output. |
| **VL**  | Caption → LLM prompt | Language polish without graph grounding. |
| **VGL** | Image ↔ Finding / Report edges | Graph-grounded summary with the lowest hallucination rate. |

## Quick start
1. **Clone & enter the project**
   ```bash
   git clone https://github.com/<your-org>/Ontology.git
   cd Ontology/grounded-ai
   ```
2. **Start services**
   ```bash
   make up
   ```
3. **Batch evaluation** (runs V / VL / VGL for all sample images)
   ```bash
   python scripts/run_eval.py --mock
   python scripts/plot_eval.py
   ```
   The commands above generate `grounded-ai/results/results.csv` and `grounded-ai/results/results_summary.png`.

When a live API is running (e.g. via `make up`), omit `--mock` to exercise the full FastAPI → Neo4j → Ollama stack.

## API smoke test (cURL)
Make sure `make up` (or `docker compose up`) is running, then walk through the edge-centric flow.

1. **Check service health**
   ```bash
   curl http://localhost:8000/health
   ```

2. **Normalise a vision caption**
   ```bash
   curl -X POST http://localhost:8000/vision/caption \
     -H "Content-Type: application/json" \
     -d '{
           "file_path": "grounded-ai/data/medical_dummy/images/img_001.png",
           "image_id": "IMG_001"
         }'
   ```
   Returns canonical `image`/`report`/`findings[]` blocks.

3. **Persist to the knowledge graph**
   ```bash
   curl -X POST http://localhost:8000/graph/upsert \
     -H "Content-Type: application/json" \
     -d '{
           "case_id": "CASE_DEMO_001",
           "image": {
             "image_id": "IMG_001",
             "path": "/data/img_001.png",
             "modality": "XR"
           },
           "report": {
             "id": "rep_demo_001",
             "text": "Chest X-ray – probable RUL nodule (~1.8 cm).",
             "model": "qwen2-vl",
             "conf": 0.83,
             "ts": "2025-10-23T12:00:00Z"
           },
           "findings": [
             {
               "id": "find_demo_001",
               "type": "nodule",
               "location": "RUL",
               "size_cm": 1.8,
               "conf": 0.87
             }
           ]
         }'
   ```
   Confirms `HAS_IMAGE`, `HAS_FINDING`, and `DESCRIBED_BY` edges are created.

4. **Fetch graph-grounded context**
   ```bash
   curl "http://localhost:8000/graph/context?image_id=IMG_001"
   ```
   Provides structured `findings`, `reports`, and human-friendly `triples[]`.

5. **Generate the final one-line impression**
   ```bash
   curl -X POST http://localhost:8000/llm/answer \
     -H "Content-Type: application/json" \
     -d '{"mode": "VGL", "image_id": "IMG_001", "style": "one_line"}'
   ```
   Swap `mode` between `V`, `VL`, and `VGL` to compare hallucination and consistency.
   `VGL` responses now return an edge-first `context_pack` (edge summary, evidence paths, facts) used for LLM prompting.

## One-shot pipeline
Trigger the entire `vLM → graph upsert → graph context → LLM` chain with a single request. The endpoint validates the vLM/LLM/Neo4j health gates, persists the case, and returns per-mode latencies plus the graph context bundle used for grounding.

```bash
curl -X POST "http://localhost:8000/pipeline/analyze?sync=true" \
  -H "Content-Type: application/json" \
  -d '{
        "case_id":"C_001",
        "file_path":"/data/medical_dummy/images/img_001.png",
        "modes":["V","VL","VGL"],
        "k":2,
        "max_chars":30,
        "fallback_to_vl":true,
        "timeout_ms":20000
      }'
```

## System Architecture
```
[Streamlit UI] → [FastAPI Orchestrator]
   ├─ vLM: Qwen2-VL / MiniCPM-V / LLaVA (captioning & VQA)
   ├─ LLM: Qwen2.5-7B-Instruct (Ollama)
   ├─ KG : Neo4j (Ontology-backed knowledge graph)
   └─ VecDB: Qdrant (text & image embedding search)
```

## Dummy Dataset (`/mnt/data/medical_dummy`)
| File              | Description                                             |
|-------------------|---------------------------------------------------------|
| `patients.csv`    | Patient demographics (sex, region, birth date, etc.)    |
| `encounters.csv`  | Inpatient/outpatient encounter history                  |
| `observations.csv`| LOINC-aligned lab and observation results               |
| `diagnoses.csv`   | ICD-10 diagnosis codes per encounter                    |
| `medications.csv` | Prescribed medications                                  |
| `imaging.csv`     | Imaging metadata and caption overlays                   |
| `ai_inference.csv`| vLM/LLM inference outputs linked to imaging             |
| `ontology_min.json`| Minimal ontology schema snapshot                       |
| `seed.cypher`     | Neo4j seed script for bootstrapping the knowledge graph |

## Ontology Snapshot
- **Entities**: Patient, Encounter, Observation, Diagnosis, Medication, Image, AIInference.
- **Relationships**:
  - `(Patient)-[:HAS_ENCOUNTER]->(Encounter)`
  - `(Encounter)-[:HAS_OBS]->(Observation)`
  - `(Encounter)-[:HAS_DX]->(Diagnosis)`
  - `(Encounter)-[:HAS_RX]->(Medication)`
  - `(Encounter)-[:HAS_IMAGE]->(Image)`
  - `(Image)-[:HAS_INFERENCE]->(AIInference)`

## Experiment Playbook
1. **vLM prompt**: “Summarize the key findings in this X-ray.”
2. **LLM reasoning**: “What follow-up tests should this patient receive?”
3. **Ontology update**: Persist vLM → LLM outputs to Neo4j and expand the graph.
4. **Composite query**: “Find hypertensive patients (I10) from the last 60 days with SBP > 140 who received antihypertensive medication.”

## Further Tasks
1. **Late Fusion**: Maintain separate LLM and vLM inference paths while refining prompt-level integration.
2. **Mid Fusion**: Prototype cross-attention or visual token injection to align latent representations between models.
3. **GraphDB Modeling**: Iterate on Neo4j schema, constraints, and data quality checks to strengthen ontology consistency.
