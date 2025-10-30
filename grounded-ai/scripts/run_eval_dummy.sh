#!/usr/bin/env bash
set -euo pipefail

# how to run:
# bash scripts/run_eval_dummy.sh [A/B/C] [top-k] [max_chars] [image_path]
# example:
# bash scripts/run_eval_dummy.sh A 2 120 /data/medical_dummy/images/api_test_data/Ultrasound-fatty-liver-Ultrasound-of-the-whole-abdomen-showing-increased-hepatic.png

DATASET="${1:-A}"   # A/B/C
K="${2:-2}"         # top-k paths
MAXC="${3:-120}"    # max_chars for triples
IMG="${4:-/data/medical_dummy/images/api_test_data/Ultrasound-fatty-liver-Ultrasound-of-the-whole-abdomen-showing-increased-hepatic.png}"

echo "[*] Resetting Neo4j database before loading ${DATASET}..."
cypher-shell -u neo4j -p test1234 "MATCH (n) DETACH DELETE n;"

echo "[*] Loading dummy ${DATASET} into Neo4j..."
case "$DATASET" in
  A) CYPHER="seed_dummy_A.cypher" ;;
  B) CYPHER="seed_dummy_B.cypher" ;;
  C) CYPHER="seed_dummy_C.cypher" ;;
  *) echo "Use A/B/C"; exit 1 ;;
esac

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CYPHER_PATH="${SCRIPT_DIR}/cyphers/${CYPHER}"

if [[ ! -f "${CYPHER_PATH}" ]]; then
  echo "[!] Cypher file not found at ${CYPHER_PATH}" >&2
  exit 1
fi

echo "[*] Applying ${CYPHER}..."
cypher-shell -u neo4j -p test1234 -f "${CYPHER_PATH}"

echo "[*] Calling /pipeline/analyze ..."
RESPONSE=$(curl -s -X POST "http://localhost:8000/pipeline/analyze?sync=true&debug=1" \
  -H "Content-Type: application/json" \
  -d "$(jq -n --arg fp "${IMG}" --argjson k ${K} --argjson mc ${MAXC} \
        '{case_id:"C_CONS", file_path:$fp, modes:["V","VL","VGL"], k:$k, max_chars:$mc, fallback_to_vl:true}')")

# 1️⃣ 전체 응답 저장 (디버그용)
LOG_DIR="${SCRIPT_DIR}/../logs"
mkdir -p "${LOG_DIR}"
OUTFILE="${LOG_DIR}/run_${DATASET}_$(date +%Y%m%d_%H%M%S).json"
echo "$RESPONSE" > "$OUTFILE"

# 2️⃣ 핵심 요약 정보 출력
echo "========== SUMMARY =========="
echo "$RESPONSE" | jq -r '.graph_context.summary[]'
echo "agreement_score: $(echo "$RESPONSE" | jq -r '.results.consensus.agreement_score')"
echo "status:          $(echo "$RESPONSE" | jq -r '.results.consensus.status')"
echo "confidence:      $(echo "$RESPONSE" | jq -r '.results.consensus.confidence')"
echo "ctx_paths_len:   $(echo "$RESPONSE" | jq -r '.debug.context_paths_len')"
echo "context_summary: $(echo "$RESPONSE" | jq -r '.debug.context_summary | join(" | ")')"

# 3️⃣ 세부 결과 블록
echo "---------- Consensus ----------"
echo "$RESPONSE" | jq '.results.consensus'
echo "---------- Debug Consensus ----------"
echo "$RESPONSE" | jq '.debug.consensus'
echo "---------- Findings (Facts) ----------"
echo "$RESPONSE" | jq '.graph_context.facts.findings'
echo "---------- Evidence Paths ----------"
echo "$RESPONSE" | jq '.debug.context_paths_head'