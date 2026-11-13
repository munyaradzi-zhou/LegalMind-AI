#!/usr/bin/env bash
# =============================================================================
# LegalMind AI — deploy to Azure (used by developers and by the CD workflow)
#
#   1. Deploy infrastructure (Bicep), keeping the images the apps currently run
#   2. Build the API and frontend images and push them to the registry
#   3. Roll both Container Apps onto the new images
#   4. Optionally seed the sample documents, then run smoke tests
#
# Usage:
#   APIM_PUBLISHER_EMAIL=you@example.com ./scripts/deploy.sh --environment dev [options]
#
# Options:
#   --environment, -e   dev | staging | prod                         (required)
#   --resource-group    Resource group name          (default: rg-legalmind-<env>)
#   --location          Region for a new resource group   (default: eastus2)
#   --tag               Image tag                  (default: current git commit)
#   --skip-infra        Skip the Bicep deployment
#   --skip-build        Skip building/pushing images (reuse --tag)
#   --seed              Upload sample_data/ through the API after deploying
#   --skip-smoke        Skip smoke tests
#
# Requires: az (logged in), docker, git, curl.
# =============================================================================
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENVIRONMENT=""
RESOURCE_GROUP=""
LOCATION="eastus2"
TAG=""
SKIP_INFRA=false
SKIP_BUILD=false
SEED=false
SKIP_SMOKE=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    -e | --environment) ENVIRONMENT="$2"; shift 2 ;;
    --resource-group) RESOURCE_GROUP="$2"; shift 2 ;;
    --location) LOCATION="$2"; shift 2 ;;
    --tag) TAG="$2"; shift 2 ;;
    --skip-infra) SKIP_INFRA=true; shift ;;
    --skip-build) SKIP_BUILD=true; shift ;;
    --seed) SEED=true; shift ;;
    --skip-smoke) SKIP_SMOKE=true; shift ;;
    -h | --help) sed -n '2,24p' "$0"; exit 0 ;;
    *) echo "Unknown option: $1" >&2; exit 1 ;;
  esac
done

[[ "${ENVIRONMENT}" =~ ^(dev|staging|prod)$ ]] || { echo "--environment must be dev, staging or prod" >&2; exit 1; }
RESOURCE_GROUP="${RESOURCE_GROUP:-rg-legalmind-${ENVIRONMENT}}"
TAG="${TAG:-$(git -C "${ROOT}" rev-parse --short=12 HEAD)}"

for cmd in az docker git curl; do
  command -v "${cmd}" > /dev/null || { echo "Missing required tool: ${cmd}" >&2; exit 1; }
done
az account show > /dev/null || { echo "Run 'az login' first." >&2; exit 1; }

step() { echo; echo "=== $* ==="; }
DEPLOYMENT_NAME="legalmind-${ENVIRONMENT}"
API_APP="ca-legalmind-api-${ENVIRONMENT}"
WEB_APP="ca-legalmind-web-${ENVIRONMENT}"

# Names and URLs are read from the resources themselves, not from deployment outputs:
# a cancelled or failed deployment has no outputs, but the resources are still there.
registry_field() { az acr list -g "${RESOURCE_GROUP}" --query "[0].$1" -o tsv 2> /dev/null || true; }
app_url() { echo "https://$(az containerapp show -g "${RESOURCE_GROUP}" -n "$1" --query properties.configuration.ingress.fqdn -o tsv)"; }

# Tag of the image an app is running now ("" if the app doesn't exist or runs the placeholder).
current_tag() {
  local image
  image=$(az containerapp show -g "${RESOURCE_GROUP}" -n "$1" --query "properties.template.containers[0].image" -o tsv 2> /dev/null || true)
  [[ "${image}" == *azurecr.io/* ]] && echo "${image##*:}" || echo ""
}

deploy_infra() { # $1 = API image tag, $2 = frontend image tag ("" = placeholder image)
  : "${APIM_PUBLISHER_EMAIL:?Set APIM_PUBLISHER_EMAIL (API Management needs a publisher email)}"
  API_IMAGE_TAG="$1" FRONTEND_IMAGE_TAG="$2" az deployment group create \
    -g "${RESOURCE_GROUP}" -n "${DEPLOYMENT_NAME}" \
    --template-file "${ROOT}/infra/main.bicep" \
    --parameters "${ROOT}/infra/parameters/${ENVIRONMENT}.bicepparam" \
    -o none
}

# ---------------------------------------------------------------------------
step "1/4 Infrastructure (${ENVIRONMENT} → ${RESOURCE_GROUP})"
# ---------------------------------------------------------------------------
# Only needed the first time (the registry must exist before images can be pushed);
# afterwards step 3 applies any infrastructure changes together with the new images.
if [[ "${SKIP_INFRA}" == false && -z "$(registry_field name)" ]]; then
  az group create -n "${RESOURCE_GROUP}" -l "${LOCATION}" --tags project=legalmind environment="${ENVIRONMENT}" -o none
  deploy_infra "" ""
else
  echo "Already provisioned; infrastructure changes are applied in step 3."
fi

REGISTRY=$(registry_field name)
LOGIN_SERVER=$(registry_field loginServer)
API_URL=$(app_url "${API_APP}")
WEB_URL=$(app_url "${WEB_APP}")

# ---------------------------------------------------------------------------
step "2/4 Images (tag ${TAG})"
# ---------------------------------------------------------------------------
if [[ "${SKIP_BUILD}" == false ]]; then
  az acr login -n "${REGISTRY}"
  docker build -t "${LOGIN_SERVER}/legalmind-api:${TAG}" -f "${ROOT}/Dockerfile" "${ROOT}"
  docker build -t "${LOGIN_SERVER}/legalmind-frontend:${TAG}" -f "${ROOT}/Dockerfile.frontend" "${ROOT}"
  docker push "${LOGIN_SERVER}/legalmind-api:${TAG}"
  docker push "${LOGIN_SERVER}/legalmind-frontend:${TAG}"
else
  echo "Skipped (deploying existing tag ${TAG})."
fi

# ---------------------------------------------------------------------------
step "3/4 Roll out"
# ---------------------------------------------------------------------------
# Image tags are commit SHAs, so git tells us whether infra/ changed since the running version.
# Unchanged (the usual case): swap the images only - fast, and the AI services aren't touched.
# Changed, first deploy (placeholder image) or unknown: re-apply the template, which sets
# image, port and health probes together.
RUNNING_TAG=$(current_tag "${API_APP}")
INFRA_CHANGED=true
if [[ -n "${RUNNING_TAG}" ]] && git -C "${ROOT}" cat-file -e "${RUNNING_TAG}^{commit}" 2> /dev/null \
  && git -C "${ROOT}" diff --quiet "${RUNNING_TAG}" "${TAG}" -- infra/; then
  INFRA_CHANGED=false
fi

if [[ "${SKIP_INFRA}" == false && "${INFRA_CHANGED}" == true ]]; then
  echo "Infrastructure changed since ${RUNNING_TAG:-the placeholder} - applying the Bicep template."
  deploy_infra "${TAG}" "${TAG}"
else
  echo "Infrastructure unchanged since ${RUNNING_TAG} - updating the container images only."
  az containerapp update -g "${RESOURCE_GROUP}" -n "${API_APP}" --image "${LOGIN_SERVER}/legalmind-api:${TAG}" -o none
  az containerapp update -g "${RESOURCE_GROUP}" -n "${WEB_APP}" --image "${LOGIN_SERVER}/legalmind-frontend:${TAG}" -o none
fi
echo "API:      ${API_URL}"
echo "Frontend: ${WEB_URL}"

# ---------------------------------------------------------------------------
step "4/4 Seed and smoke tests"
# ---------------------------------------------------------------------------
# The apps scale to zero when idle: wait for the new revision to answer.
for _ in $(seq 1 36); do
  curl -sf "${API_URL}/health" > /dev/null && break
  sleep 5
done

if [[ "${SEED}" == true ]]; then
  APP_URL="${API_URL}" bash "${ROOT}/scripts/seed-data.sh"
fi
if [[ "${SKIP_SMOKE}" == false ]]; then
  APP_URL="${API_URL}" bash "${ROOT}/scripts/run-smoke-tests.sh"
fi

if [[ -n "${GITHUB_OUTPUT:-}" ]]; then
  { echo "api-url=${API_URL}"; echo "frontend-url=${WEB_URL}"; } >> "${GITHUB_OUTPUT}"
fi
echo
echo "Deployed ${TAG} to ${ENVIRONMENT}: ${WEB_URL}"
