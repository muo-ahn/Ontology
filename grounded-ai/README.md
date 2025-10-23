# Ontology + vLM + LLM Local Prototype

## Overview
- Prototype playground for experimenting with a hybrid Ontology + vLM + LLM stack purely on a local workstation.
- Target hardware: RTX 4070 Laptop GPU and Apple Silicon (M4) with Metal acceleration.
- Goal: validate the end-to-end orchestration flow (Streamlit UI → FastAPI → vLM → LLM → Neo4j/Qdrant) against a controlled medical dummy dataset.

## Quick start
1. **Start services**
   ```bash
   make up
   ```
2. **Seed/verify models** (optional if Ollama cache already populated)
   ```bash
   make pull
   ```
3. **Batch evaluation** (runs V / V+L / V→G→L for all sample images)
   ```bash
   python scripts/run_eval.py --mock
   python scripts/plot_eval.py
   ```
   The commands above generate `grounded-ai/results/results.csv` and `grounded-ai/results/results_summary.png`.

When a live API is running (e.g. via `make up`), omit `--mock` to exercise the full FastAPI → Neo4j → Ollama stack.

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
