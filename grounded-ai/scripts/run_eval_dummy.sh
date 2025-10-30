#!/usr/bin/env bash

set -euo pipefail

# Configure how cypher queries will be executed (local binary vs docker exec).
CYPHER_MODE=""
CYPHER_SHELL_BIN=""
DOCKER_COMPOSE_CMD=()

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
COMPOSE_FILE="${PROJECT_ROOT}/docker-compose.yml"

die() {
  echo "[!] $*" >&2
  exit 1
}

detect_docker_compose() {
  if command -v docker >/dev/null 2>&1; then
    if docker compose version >/dev/null 2>&1; then
      DOCKER_COMPOSE_CMD=(docker compose)
      return 0
    fi
  fi

  if command -v docker-compose >/dev/null 2>&1; then
    DOCKER_COMPOSE_CMD=(docker-compose)
    return 0
  fi

  return 1
}

detect_cypher_backend() {
  if [ -n "${CYPHER_SHELL:-}" ]; then
    if [ -x "${CYPHER_SHELL}" ] || [ -f "${CYPHER_SHELL}" ]; then
      CYPHER_MODE="local"
      CYPHER_SHELL_BIN="${CYPHER_SHELL}"
      return 0
    fi
    die "CYPHER_SHELL is set to '${CYPHER_SHELL}' but it is not executable."
  fi

  local candidate
  for candidate in cypher-shell cypher-shell.bat cypher-shell.cmd; do
    if candidate_path="$(command -v "${candidate}" 2>/dev/null)"; then
      CYPHER_MODE="local"
      CYPHER_SHELL_BIN="${candidate_path}"
      return 0
    fi
  done

  if [ -n "${NEO4J_HOME:-}" ]; then
    for candidate in cypher-shell cypher-shell.bat cypher-shell.cmd; do
      if [ -x "${NEO4J_HOME}/bin/${candidate}" ] || [ -f "${NEO4J_HOME}/bin/${candidate}" ]; then
        CYPHER_MODE="local"
        CYPHER_SHELL_BIN="${NEO4J_HOME}/bin/${candidate}"
        return 0
      fi
    done
  fi

  if detect_docker_compose; then
    CYPHER_MODE="docker"
    return 0
  fi

  return 1
}

ensure_docker_ready() {
  if [ ! -f "${COMPOSE_FILE}" ]; then
    die "Expected docker-compose.yml at ${PROJECT_ROOT}, but it was not found."
  fi

  local running_services
  running_services="$("${DOCKER_COMPOSE_CMD[@]}" -f "${COMPOSE_FILE}" ps --status running --services 2>/dev/null)" || {
    die "Failed to query docker compose. Is Docker running?"
  }

  if ! printf '%s\n' "${running_services}" | grep -qx 'neo4j'; then
    die "Neo4j container is not running. Start it with 'docker compose -f ${COMPOSE_FILE} up -d neo4j'."
  fi
}

run_cypher() {
  if [ "${CYPHER_MODE}" = "docker" ]; then
    "${DOCKER_COMPOSE_CMD[@]}" -f "${COMPOSE_FILE}" exec -T neo4j cypher-shell "$@"
    return
  fi

  case "${CYPHER_SHELL_BIN##*.}" in
    bat|BAT|cmd|CMD)
      local win_path="${CYPHER_SHELL_BIN}"
      if command -v cygpath >/dev/null 2>&1; then
        win_path="$(cygpath -w "${CYPHER_SHELL_BIN}")"
      fi
      cmd.exe /C "${win_path}" "$@"
      ;;
    *)
      "${CYPHER_SHELL_BIN}" "$@"
      ;;
  esac
}

run_cypher_file() {
  local file_path="$1"
  shift
  if [ "${CYPHER_MODE}" = "docker" ]; then
    "${DOCKER_COMPOSE_CMD[@]}" -f "${COMPOSE_FILE}" exec -T neo4j cypher-shell "$@" < "${file_path}"
    return
  fi
  run_cypher "$@" -f "${file_path}"
}

detect_cypher_backend || die "cypher-shell executable not found. Install Neo4j CLI tools or set the CYPHER_SHELL env var, or ensure Docker Compose is available."

if [ "${CYPHER_MODE}" = "docker" ]; then
  ensure_docker_ready
  echo "[*] Using dockerized cypher-shell via ${DOCKER_COMPOSE_CMD[*]} exec neo4j ..."
fi

# how to run:
# bash scripts/run_eval_dummy.sh [A/B/C] [top-k] [max_chars] [image_path]
# example:
# bash scripts/run_eval_dummy.sh A 2 120 /data/medical_dummy/images/api_test_data/Ultrasound-fatty-liver-Ultrasound-of-the-whole-abdomen-showing-increased-hepatic.png

DATASET="${1:-A}"   # A/B/C
K="${2:-2}"         # top-k paths
MAXC="${3:-120}"    # max_chars for triples
IMG="${4:-/data/medical_dummy/images/api_test_data/Ultrasound-fatty-liver-Ultrasound-of-the-whole-abdomen-showing-increased-hepatic.png}"

echo "[*] Resetting Neo4j database before loading ${DATASET}..."
run_cypher -u neo4j -p test1234 "MATCH (n) DETACH DELETE n;"

echo "[*] Loading dummy ${DATASET} into Neo4j..."
case "$DATASET" in
  A) CYPHER="seed_dummy_A.cypher" ;;
  B) CYPHER="seed_dummy_B.cypher" ;;
  C) CYPHER="seed_dummy_C.cypher" ;;
  *) echo "Use A/B/C"; exit 1 ;;
esac

CYPHER_PATH="${SCRIPT_DIR}/cyphers/${CYPHER}"

if [ ! -f "${CYPHER_PATH}" ]; then
  echo "[!] Cypher file not found at ${CYPHER_PATH}" >&2
  exit 1
fi

echo "[*] Applying ${CYPHER}..."
run_cypher_file "${CYPHER_PATH}" -u neo4j -p test1234

echo "[*] Running sanity cypher (top 3 images by findings)..."
run_cypher -u neo4j -p test1234 "MATCH (i:Image)-[:HAS_FINDING]->(f) RETURN i.image_id, count(f) AS c ORDER BY c DESC LIMIT 3;"

echo "[*] Calling /pipeline/analyze ..."
RESPONSE=$(curl -s -X POST "http://localhost:8000/pipeline/analyze?sync=true&debug=1" \
  -H "Content-Type: application/json" \
  -d "$(jq -n --arg fp "${IMG}" --argjson k ${K} --argjson mc ${MAXC} \
        '{case_id:"C_CONS", file_path:$fp, modes:["V","VL","VGL"], k:$k, max_chars:$mc, fallback_to_vl:true}')")

# 1️⃣ 전체 응답 저장 (디버그용)
LOG_DIR="${SCRIPT_DIR}/../logs"
mkdir -p "${LOG_DIR}"
TIMESTAMP="$(date +%Y%m%d_%H%M%S)"
OUTFILE="${LOG_DIR}/run_${DATASET}_${TIMESTAMP}.json"
echo "$RESPONSE" > "$OUTFILE"

# 2️⃣ 핵심 요약 정보 출력
echo "========== SUMMARY =========="
echo "$RESPONSE" | jq -r '.graph_context.summary[]'
AGREEMENT="$(echo "$RESPONSE" | jq -r '.results.consensus.agreement_score')"
STATUS="$(echo "$RESPONSE" | jq -r '.results.consensus.status')"
CONFIDENCE="$(echo "$RESPONSE" | jq -r '.results.consensus.confidence')"
CTX_PATHS="$(echo "$RESPONSE" | jq -r '.debug.context_paths_len')"

echo "agreement_score: ${AGREEMENT}"
echo "status:          ${STATUS}"
echo "confidence:      ${CONFIDENCE}"
echo "ctx_paths_len:   ${CTX_PATHS}"
echo "context_summary: $(echo "$RESPONSE" | jq -r '.debug.context_summary | join(" | ")')"

SUMMARY_FILE="${LOG_DIR}/summary.csv"
echo "${TIMESTAMP},${DATASET},${AGREEMENT},${STATUS},${CONFIDENCE},${CTX_PATHS}" >> "${SUMMARY_FILE}"

# 3️⃣ 세부 결과 블록
echo "---------- Consensus ----------"
echo "$RESPONSE" | jq '.results.consensus'
echo "---------- Debug Consensus ----------"
echo "$RESPONSE" | jq '.debug.consensus'
echo "---------- Findings (Facts) ----------"
echo "$RESPONSE" | jq '.graph_context.facts.findings'
echo "---------- Evidence Paths ----------"
echo "$RESPONSE" | jq '.debug.context_paths_head'
