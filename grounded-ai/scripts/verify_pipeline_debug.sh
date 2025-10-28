#!/usr/bin/env bash
set -euo pipefail

API_URL="${API_URL:-http://localhost:8000}"
IMAGE_PATH="${1:-${PIPELINE_IMAGE_PATH:-./data/medical_dummy/images/img_001.png}}"
QUERY_PARAMS="sync=true&debug=1"

if [[ ! -f "${IMAGE_PATH}" ]]; then
  echo "❌ Image not found at ${IMAGE_PATH}" >&2
  exit 1
fi

image_b64=$(base64 < "${IMAGE_PATH}" | tr -d '\n')

payload=$(jq -n \
  --arg image_b64 "${image_b64}" \
  --argjson modes '["V","VL","VGL"]' \
  '{
    image_b64: $image_b64,
    file_path: null,
    modes: $modes,
    k: 2,
    max_chars: 30,
    fallback_to_vl: true
  }')

echo "🔍 Checking pipeline debug fields @ ${API_URL}/pipeline/analyze (${IMAGE_PATH})"

diag_payload=$(curl -sf "${API_URL}/__diag/whoami" || true)
if [[ -n "${diag_payload}" ]]; then
  echo "ℹ️  __diag/whoami → ${diag_payload}"
else
  echo "⚠️  __diag/whoami unavailable (proceeding without router metadata)"
fi

tmp_resp=$(mktemp)
http_status=$(curl -sS -o "${tmp_resp}" -w "%{http_code}" -X POST "${API_URL}/pipeline/analyze?${QUERY_PARAMS}" \
  -H "Content-Type: application/json" \
  -d "${payload}")

response=$(cat "${tmp_resp}")
rm -f "${tmp_resp}"

if [[ -z "${response}" ]]; then
  echo "❌ Empty response from pipeline/analyze" >&2
  exit 1
fi

if [[ "${http_status}" -ge 400 ]]; then
  echo "❌ pipeline/analyze returned HTTP ${http_status}" >&2
  echo "${response}" | jq . 2>/dev/null || echo "${response}"
  exit 1
fi

diagnostics=$(jq '{
  pre_upsert_findings_len: .debug.pre_upsert_findings_len,
  upsert_receipt: .debug.upsert_receipt,
  post_upsert_finding_ids: .debug.post_upsert_finding_ids,
  context_findings_len: .debug.context_findings_len,
  context_paths_len: .debug.context_paths_len
}' <<< "${response}")

echo "${diagnostics}"

for key in pre_upsert_findings_len upsert_receipt post_upsert_finding_ids context_findings_len context_paths_len; do
  value=$(jq -e --arg k "${key}" '.[$k]' <<< "${diagnostics}" 2>/dev/null || echo "null")
  if [[ "${value}" == "null" ]]; then
    echo "❌ Missing debug value: ${key}" >&2
    exit 1
  fi
done

echo "✅ Debug fields populated successfully."
