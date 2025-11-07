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

echo "=== [8] Vision Pipeline Debug Query ==="
curl -sS -X POST "http://localhost:8000/pipeline/analyze?sync=true&debug=1" \
  -H 'Content-Type: application/json' \
  -d "{
        \"file_path\": \"$FILE_PATH\",
        \"modes\": [\"V\", \"VL\", \"VGL\"],
        \"k\": 2,
        \"max_chars\": 120
      }" | jq '.debug'

echo ""
echo "=== [9] Debug with parameters ==="
curl -sS -X POST "http://localhost:8000/pipeline/analyze?sync=true&debug=1" \
  -H 'Content-Type: application/json' \
  -d "{
        \"file_path\": \"$FILE_PATH\",
        \"modes\": [\"V\", \"VL\", \"VGL\"],
        \"k\": 2,
        \"max_chars\": 120,
        \"parameters\": $PARAMETERS
      }" | jq '{finding_fallback: .debug.finding_fallback, finding_source: .results.finding_source, seeded_ids: .results.seeded_finding_ids}'

echo ""
echo "=== [10-1] E2E sync test (with parameters) ==="
curl -sS -X POST "http://localhost:8000/pipeline/analyze?sync=true&debug=1" \
  -H 'Content-Type: application/json' \
  -d "{
        \"file_path\": \"$FILE_PATH\",
        \"modes\": [\"V\", \"VL\", \"VGL\"],
        \"k\": 2,
        \"max_chars\": 120,
        \"parameters\": $PARAMETERS
      }"

echo ""
echo "=== [10-2] E2E sync test (no parameters, with jq filter) ==="
curl -sS -X POST "http://localhost:8000/pipeline/analyze?sync=true&debug=1" \
  -H 'Content-Type: application/json' \
  -d "{
        \"file_path\": \"$FILE_PATH\",
        \"modes\": [\"V\", \"VL\", \"VGL\"],
        \"k\": 2,
        \"max_chars\": 120
      }" | jq '{slots: .debug.context_slot_limits, paths: .graph_context.paths}'
