#!/usr/bin/env bash
# =============================================================================
# LegalMind AI — Entra ID sign-in and matter groups (run once per environment)
#
# Creates, or updates when they exist:
#   - the "LegalMind API (<env>)" app registration: app roles Lawyer, Paralegal,
#     Admin (users) and Service (applications), a delegated scope access_as_user,
#     v2 access tokens with the user's security groups in the "groups" claim, and
#     the Azure CLI pre-authorised so testers can get a token with az
#   - its service principal, with assignment required (only users and apps given a
#     role can get a token for the API)
#   - one security group per matter in corpus/manifest.json ("LegalMind matter …")
#
# Prints the settings for the API (AZURE_API_AUDIENCE, MATTER_GROUPS); pass them
# to deploy.sh (it reads them from the environment) and re-run
# scripts/backfill_access.py so indexed chunks carry the groups.
#
# Entra ID objects can't be declared in Bicep without the preview Graph extension,
# so this is a script. It needs a directory role that can create applications and
# groups (e.g. Application Administrator + Groups Administrator).
#
# Usage:
#   ./scripts/setup_entra.sh --environment dev
#   ./scripts/setup_entra.sh -e dev --assign <user or app object id> <role> [matter ...]
#   ./scripts/setup_entra.sh -e dev --unassign <user object id> <matter ...>
# =============================================================================
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENVIRONMENT=""
ACTION="setup"
PRINCIPAL=""
ROLE=""
MATTERS=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    -e | --environment) ENVIRONMENT="$2"; shift 2 ;;
    --assign) ACTION="assign"; PRINCIPAL="$2"; ROLE="$3"; shift 3; MATTERS=("$@"); break ;;
    --unassign) ACTION="unassign"; PRINCIPAL="$2"; shift 2; MATTERS=("$@"); break ;;
    -h | --help) sed -n '2,27p' "$0"; exit 0 ;;
    *) echo "Unknown option: $1" >&2; exit 1 ;;
  esac
done
[[ "${ENVIRONMENT}" =~ ^(dev|staging|prod)$ ]] || { echo "--environment must be dev, staging or prod" >&2; exit 1; }

APP_NAME="LegalMind API (${ENVIRONMENT})"
GRAPH="https://graph.microsoft.com/v1.0"
AZURE_CLI_APP_ID="04b07795-8ddb-461a-bbee-02f9e1bf7b46"

# Fixed IDs, so re-running the script updates the roles and scope instead of adding new ones.
SCOPE_ID="6c7a1d52-3f0e-4c1b-9a63-0b8f3d1e6a01"
ROLE_LAWYER="0f3c2a51-7d4e-4b8a-9c1f-1a2b3c4d5e01"
ROLE_PARALEGAL="0f3c2a51-7d4e-4b8a-9c1f-1a2b3c4d5e02"
ROLE_ADMIN="0f3c2a51-7d4e-4b8a-9c1f-1a2b3c4d5e03"
ROLE_SERVICE="0f3c2a51-7d4e-4b8a-9c1f-1a2b3c4d5e04"

role_id() {
  case "$1" in
    Lawyer) echo "${ROLE_LAWYER}" ;;
    Paralegal) echo "${ROLE_PARALEGAL}" ;;
    Admin) echo "${ROLE_ADMIN}" ;;
    Service) echo "${ROLE_SERVICE}" ;;
    *) echo "Unknown role $1 (Lawyer, Paralegal, Admin, Service)" >&2; exit 1 ;;
  esac
}

group_name() { echo "LegalMind matter $1"; }

group_id() {
  az ad group list --filter "displayName eq '$(group_name "$1")'" --query "[0].id" -o tsv
}

app_id() { az ad app list --filter "displayName eq '${APP_NAME}'" --query "[0].appId" -o tsv; }

sp_id() { az ad sp list --filter "appId eq '$1'" --query "[0].id" -o tsv; }

body_file() { mktemp "${TMPDIR:-/tmp}/entra.XXXXXX.json"; }

setup() {
  local app obj sp body
  app="$(app_id)"
  if [[ -z "${app}" ]]; then
    app="$(az ad app create --display-name "${APP_NAME}" --sign-in-audience AzureADMyOrg --query appId -o tsv)"
    echo "Created app registration ${APP_NAME} (${app})"
  else
    echo "App registration ${APP_NAME} exists (${app})"
  fi
  obj="$(az ad app show --id "${app}" --query id -o tsv)"

  role() { # value, id, description, member type
    printf '{"id":"%s","value":"%s","displayName":"%s","description":"%s","allowedMemberTypes":["%s"],"isEnabled":true}' \
      "$2" "$1" "$1" "$3" "$4"
  }
  body="$(body_file)"
  cat > "${body}" <<JSON
{
  "identifierUris": ["api://${app}"],
  "groupMembershipClaims": "SecurityGroup",
  "api": {
    "requestedAccessTokenVersion": 2,
    "oauth2PermissionScopes": [{
      "id": "${SCOPE_ID}", "value": "access_as_user", "type": "User", "isEnabled": true,
      "adminConsentDisplayName": "Use LegalMind as the signed-in user",
      "adminConsentDescription": "Ask questions and search the documents the signed-in user may see.",
      "userConsentDisplayName": "Use LegalMind as you",
      "userConsentDescription": "Ask questions and search the documents you may see."
    }]
  },
  "appRoles": [
    $(role Lawyer "${ROLE_LAWYER}" "Sees the files of their matters, including privileged documents" User),
    $(role Paralegal "${ROLE_PARALEGAL}" "Sees the files of their matters, except privileged documents" User),
    $(role Admin "${ROLE_ADMIN}" "Adds documents to the knowledge base" User),
    $(role Service "${ROLE_SERVICE}" "An application that may call the API; sees public law only" Application)
  ]
}
JSON
  az rest --method PATCH --uri "${GRAPH}/applications/${obj}" --headers "Content-Type=application/json" --body "@${body}"
  # The scope must exist before an application can be pre-authorised for it.
  printf '{"api":{"preAuthorizedApplications":[{"appId":"%s","delegatedPermissionIds":["%s"]}]}}' \
    "${AZURE_CLI_APP_ID}" "${SCOPE_ID}" > "${body}"
  az rest --method PATCH --uri "${GRAPH}/applications/${obj}" --headers "Content-Type=application/json" --body "@${body}"
  rm -f "${body}"
  echo "Configured app roles, the access_as_user scope and the groups claim"

  sp="$(sp_id "${app}")"
  if [[ -z "${sp}" ]]; then
    sp="$(az ad sp create --id "${app}" --query id -o tsv)"
  fi
  az ad sp update --id "${sp}" --set appRoleAssignmentRequired=true
  echo "Service principal ${sp}: role assignment required"

  local matters gid json="{"
  mapfile -t matters < <(python -c "
import json, sys
docs = json.load(open(sys.argv[1], encoding='utf-8'))['documents']
print('\n'.join(sorted({d['matter'] for d in docs if d.get('matter')})))" "${ROOT}/corpus/manifest.json")
  for matter in "${matters[@]}"; do
    gid="$(group_id "${matter}")"
    if [[ -z "${gid}" ]]; then
      gid="$(az ad group create --display-name "$(group_name "${matter}")" \
        --mail-nickname "legalmind-$(echo "${matter}" | tr -c 'A-Za-z0-9' '-' | tr '[:upper:]' '[:lower:]' | sed 's/-*$//')" \
        --description "Members may see the files of matter ${matter} in LegalMind" --query id -o tsv)"
      echo "Created group $(group_name "${matter}") (${gid})"
    fi
    json+="\"${matter}\":\"${gid}\","
  done
  json="${json%,}}"

  echo ""
  echo "Settings for the API (deploy.sh reads them from the environment):"
  echo "  export AZURE_API_AUDIENCE='${app},api://${app}'"
  echo "  export MATTER_GROUPS='${json}'"
  echo "Get a token to test with (after --assign):"
  echo "  az account get-access-token --scope api://${app}/access_as_user --query accessToken -o tsv"
}

assign() {
  local app sp gid
  app="$(app_id)"; [[ -n "${app}" ]] || { echo "Run the setup first" >&2; exit 1; }
  sp="$(sp_id "${app}")"
  if ! az rest --method GET --uri "${GRAPH}/servicePrincipals/${sp}/appRoleAssignedTo" \
    --query "value[?principalId=='${PRINCIPAL}' && appRoleId=='$(role_id "${ROLE}")'] | [0].id" -o tsv | grep -q .; then
    az rest --method POST --uri "${GRAPH}/servicePrincipals/${sp}/appRoleAssignedTo" \
      --headers "Content-Type=application/json" \
      --body "{\"principalId\":\"${PRINCIPAL}\",\"resourceId\":\"${sp}\",\"appRoleId\":\"$(role_id "${ROLE}")\"}" > /dev/null
  fi
  echo "${PRINCIPAL}: role ${ROLE}"
  for matter in "${MATTERS[@]}"; do
    gid="$(group_id "${matter}")"; [[ -n "${gid}" ]] || { echo "No group for matter ${matter}" >&2; exit 1; }
    if [[ "$(az ad group member check --group "${gid}" --member-id "${PRINCIPAL}" --query value -o tsv)" != "true" ]]; then
      az ad group member add --group "${gid}" --member-id "${PRINCIPAL}"
    fi
    echo "${PRINCIPAL}: member of $(group_name "${matter}")"
  done
}

unassign() {
  local gid
  for matter in "${MATTERS[@]}"; do
    gid="$(group_id "${matter}")"
    if [[ -n "${gid}" && "$(az ad group member check --group "${gid}" --member-id "${PRINCIPAL}" --query value -o tsv)" == "true" ]]; then
      az ad group member remove --group "${gid}" --member-id "${PRINCIPAL}"
    fi
    echo "${PRINCIPAL}: not a member of $(group_name "${matter}")"
  done
}

"${ACTION}"
