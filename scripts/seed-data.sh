#!/usr/bin/env bash
# =============================================================================
# LegalMind AI — Seed Sample Legal Documents
# =============================================================================
# Uploads sample documents from sample_data/ to the LegalMind API ingest
# endpoint. Used for initial data seeding in dev/staging environments.
# Re-running it replaces each document's earlier chunks (replace_existing),
# so seeding twice doesn't create duplicates.
#
# Prerequisites:
#   - curl installed
#   - The LegalMind API is running and accessible at APP_URL
#   - Sample documents exist in sample_data/
#
# Usage:
#   APP_URL=https://app.example.com ./seed-data.sh [--directory path]
# =============================================================================

set -euo pipefail

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

APP_URL="${APP_URL:?APP_URL environment variable is required}"
SAMPLE_DATA_DIR="${SAMPLE_DATA_DIR:-${PROJECT_ROOT}/sample_data}"
API_TOKEN="${API_TOKEN:-}"
MAX_RETRIES="${MAX_RETRIES:-3}"
RETRY_DELAY="${RETRY_DELAY:-5}"

# Parse arguments
while [[ $# -gt 0 ]]; do
  case "$1" in
    --directory|-d)
      SAMPLE_DATA_DIR="$2"
      shift 2
      ;;
    --url|-u)
      APP_URL="$2"
      shift 2
      ;;
    --token|-t)
      API_TOKEN="$2"
      shift 2
      ;;
    --help|-h)
      echo "Usage: $0 [--directory path] [--url app_url] [--token api_token]"
      echo ""
      echo "Options:"
      echo "  --directory, -d   Path to sample data directory (default: sample_data/)"
      echo "  --url, -u         Application URL"
      echo "  --token, -t       API bearer token for authentication"
      echo "  --help, -h        Show this help message"
      exit 0
      ;;
    *)
      echo "ERROR: Unknown option: $1" >&2
      exit 1
      ;;
  esac
done

# Strip trailing slash from APP_URL
APP_URL="${APP_URL%/}"

echo "============================================="
echo "  LegalMind AI — Seed Data"
echo "  API URL: ${APP_URL}"
echo "  Data directory: ${SAMPLE_DATA_DIR}"
echo "============================================="

# ---------------------------------------------------------------------------
# Validate prerequisites
# ---------------------------------------------------------------------------
echo ""
echo "--- Validating prerequisites ---"

if ! command -v curl &>/dev/null; then
  echo "ERROR: curl is not installed" >&2
  exit 1
fi

if [[ ! -d "${SAMPLE_DATA_DIR}" ]]; then
  echo "ERROR: Sample data directory does not exist: ${SAMPLE_DATA_DIR}" >&2
  exit 1
fi

# Count files to upload
SUPPORTED_EXTENSIONS=("pdf" "docx" "txt" "png" "jpg" "jpeg" "tiff")
FILE_COUNT=0
for ext in "${SUPPORTED_EXTENSIONS[@]}"; do
  count=$(find "${SAMPLE_DATA_DIR}" -maxdepth 2 -name "*.${ext}" -type f 2>/dev/null | wc -l)
  FILE_COUNT=$((FILE_COUNT + count))
done

if [[ ${FILE_COUNT} -eq 0 ]]; then
  echo "WARNING: No supported files found in ${SAMPLE_DATA_DIR}"
  echo "  Supported extensions: ${SUPPORTED_EXTENSIONS[*]}"
  exit 0
fi

echo "  Found ${FILE_COUNT} file(s) to upload"

# ---------------------------------------------------------------------------
# Wait for API to be healthy
# ---------------------------------------------------------------------------
echo ""
echo "--- Checking API health ---"

HEALTH_URL="${APP_URL}/health"
HEALTH_RETRIES=10
HEALTH_DELAY=5

for i in $(seq 1 ${HEALTH_RETRIES}); do
  HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" \
    --max-time 10 \
    "${HEALTH_URL}" 2>/dev/null || echo "000")

  if [[ "${HTTP_CODE}" == "200" ]]; then
    echo "  API is healthy (HTTP ${HTTP_CODE})"
    break
  fi

  if [[ ${i} -eq ${HEALTH_RETRIES} ]]; then
    echo "ERROR: API is not healthy after ${HEALTH_RETRIES} attempts (last HTTP ${HTTP_CODE})" >&2
    exit 1
  fi

  echo "  Waiting for API... attempt ${i}/${HEALTH_RETRIES} (HTTP ${HTTP_CODE})"
  sleep "${HEALTH_DELAY}"
done

# ---------------------------------------------------------------------------
# Upload files
# ---------------------------------------------------------------------------
echo ""
echo "--- Uploading sample documents ---"

INGEST_URL="${APP_URL}/api/ingest"
SUCCESS_COUNT=0
FAILURE_COUNT=0
FAILED_FILES=()

# Build auth header if token provided
AUTH_HEADER=""
if [[ -n "${API_TOKEN}" ]]; then
  AUTH_HEADER="Authorization: Bearer ${API_TOKEN}"
fi

# sample_data/<folder>/ → the API's document_type
document_type_for() {
  case "$(basename "$(dirname "$1")")" in
    correspondence) echo "correspondence" ;;
    case_files) echo "case_file" ;;
    policies) echo "policy" ;;
    *) echo "contract" ;;
  esac
}

upload_file() {
  local file_path="$1"
  local file_name
  file_name="$(basename "${file_path}")"
  local attempt=0

  while [[ ${attempt} -lt ${MAX_RETRIES} ]]; do
    attempt=$((attempt + 1))

    CURL_ARGS=(-s -w "\n%{http_code}" --max-time 300)
    if [[ -n "${AUTH_HEADER}" ]]; then
      CURL_ARGS+=(-H "${AUTH_HEADER}")
    fi
    CURL_ARGS+=(-F "file=@${file_path}" -F "document_type=$(document_type_for "${file_path}")" -F "language=nl" -F "replace_existing=true")

    RESPONSE=$(curl "${CURL_ARGS[@]}" "${INGEST_URL}" 2>/dev/null || echo -e "\n000")

    # Extract HTTP code (last line) and body (everything else)
    HTTP_CODE=$(echo "${RESPONSE}" | tail -n1)
    BODY=$(echo "${RESPONSE}" | head -n -1)

    if [[ "${HTTP_CODE}" =~ ^2[0-9][0-9]$ ]]; then
      echo "  OK  [${HTTP_CODE}] ${file_name}"
      return 0
    fi

    if [[ ${attempt} -lt ${MAX_RETRIES} ]]; then
      echo "  RETRY [${HTTP_CODE}] ${file_name} (attempt ${attempt}/${MAX_RETRIES})"
      sleep "${RETRY_DELAY}"
    fi
  done

  echo "  FAIL [${HTTP_CODE}] ${file_name} — ${BODY}"
  return 1
}

for ext in "${SUPPORTED_EXTENSIONS[@]}"; do
  while IFS= read -r -d '' file_path; do
    if upload_file "${file_path}"; then
      SUCCESS_COUNT=$((SUCCESS_COUNT + 1))
    else
      FAILURE_COUNT=$((FAILURE_COUNT + 1))
      FAILED_FILES+=("$(basename "${file_path}")")
    fi
  done < <(find "${SAMPLE_DATA_DIR}" -maxdepth 2 -name "*.${ext}" -type f -print0 2>/dev/null)
done

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
echo ""
echo "============================================="
echo "  Seed Data Summary"
echo "  Total:     ${FILE_COUNT}"
echo "  Succeeded: ${SUCCESS_COUNT}"
echo "  Failed:    ${FAILURE_COUNT}"

if [[ ${FAILURE_COUNT} -gt 0 ]]; then
  echo ""
  echo "  Failed files:"
  for f in "${FAILED_FILES[@]}"; do
    echo "    - ${f}"
  done
  echo "============================================="
  exit 1
fi

echo "============================================="
