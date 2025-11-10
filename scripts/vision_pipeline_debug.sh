#!/bin/bash
# Usage:
#   ./vision_pipeline_debug.sh <file_path> <parameters_json>
# Example:
#   ./vision_pipeline_debug.sh "/data/medical_dummy/images/api_test_data/Ultrasound-fatty-liver-Ultrasound-of-the-whole-abdomen-showing-increased-hepatic.png" '{"force_dummy_fallback": true}'
#   or (no parameters)
#   ./vision_pipeline_debug.sh "/data/medical_dummy/images/api_test_data/Ultrasound-fatty-liver-Ultrasound-of-the-whole-abdomen-showing-increased-hepatic.png" "{}"

# Argument check
if [ "$#" -lt 2 ]; then
  echo "Usage: $0 <file_path> <parameters_json>"
  exit 1
fi

FILE_PATH="$1"
PARAMETERS="$2"

# Validate parameter JSON (must be an object; "{}" allowed)
if ! echo "$PARAMETERS" | jq -e 'type == "object"' >/dev/null 2>&1; then
  echo "parameters_json must be a valid JSON object" >&2
  exit 1
fi

HAS_CUSTOM_PARAMS=false
if echo "$PARAMETERS" | jq -e 'keys | length > 0' >/dev/null 2>&1; then
  HAS_CUSTOM_PARAMS=true
fi

build_body() {
  local include_params="$1"
  if [ "$include_params" = "with_params" ]; then
    jq -n \
      --arg path "$FILE_PATH" \
      --argjson params "$PARAMETERS" \
      '{
        file_path: $path,
        modes: ["V","VL","VGL"],
        k: 2,
        max_chars: 120,
        parameters: ($params // {})
      }'
  else
    jq -n \
      --arg path "$FILE_PATH" \
      '{
        file_path: $path,
        modes: ["V","VL","VGL"],
        k: 2,
        max_chars: 120
      }'
  fi
}

PRIMARY_MODE="without_params"
if [ "$HAS_CUSTOM_PARAMS" = true ]; then
  PRIMARY_MODE="with_params"
fi

echo "=== [8] Vision Pipeline Debug Query ==="
curl -sS -X POST "http://localhost:8000/pipeline/analyze?sync=true&debug=1" \
  -H 'Content-Type: application/json' \
  -d "$(build_body "$PRIMARY_MODE")" | jq '.debug'

echo ""
echo "=== [9] Debug with parameters ==="
if [ "$HAS_CUSTOM_PARAMS" = true ]; then
  curl -sS -X POST "http://localhost:8000/pipeline/analyze?sync=true&debug=1" \
    -H 'Content-Type: application/json' \
    -d "$(build_body with_params)" | jq '{finding_fallback: .debug.finding_fallback, finding_source: .results.finding_source, seeded_ids: .results.seeded_finding_ids}'
else
  echo '{"info":"no parameters supplied"}'
fi

echo ""
echo "=== [10-1] E2E sync test (with parameters) ==="
curl -sS -X POST "http://localhost:8000/pipeline/analyze?sync=true&debug=1" \
  -H 'Content-Type: application/json' \
  -d "$(build_body "$PRIMARY_MODE")"

echo ""
echo "=== [10-2] E2E sync test (no parameters, with jq filter) ==="
curl -sS -X POST "http://localhost:8000/pipeline/analyze?sync=true&debug=1" \
  -H 'Content-Type: application/json' \
  -d "$(build_body without_params)" | jq '{slots: .debug.context_slot_limits, paths: .graph_context.paths}'
