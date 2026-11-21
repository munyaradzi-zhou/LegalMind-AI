# ADR 0007: One user-assigned managed identity, and no keys

- Status: accepted
- Date: 2026-11-21 (decided at the start of the project, recorded now)

## Context

The API talks to eight Azure services: OpenAI, AI Search, Language, Document Intelligence,
Content Understanding, Content Safety, Cosmos DB and Blob Storage. The usual way is a key
or connection string per service, kept in Key Vault or in app settings. Keys leak (logs,
screenshots, `.env` files), must be rotated, and grant everything to whoever holds them.
The data is a law firm's client files.

## Decision

- **No keys between our services.** Every service has key authentication **switched off**
  in Bicep (`disableLocalAuth: true` on OpenAI, AI Search, Cosmos DB and the Cognitive
  Services accounts; `allowSharedKeyAccess: false` on Storage; `adminUserEnabled: false` on
  the registry; RBAC-only Key Vault). A leaked key would be useless; there are none to leak.
- **One user-assigned managed identity** (`id-legalmind-<env>`) for the API, the web app
  and the MCP server. Each service module grants it the narrowest data role it needs
  (e.g. Cognitive Services OpenAI User, Search Index Data Contributor, Storage Blob Data
  Contributor, the Cosmos DB SQL data contributor role), so a module can't be deployed
  without its permission.
- **Code** uses `DefaultAzureCredential`: the managed identity in Azure, the developer's
  `az login` locally. The same code, no secrets in either place.
- **CI/CD** signs in with GitHub OIDC (a federated credential on an app registration): no
  stored client secret.
- **Users** sign in with Entra ID (ADR 0001); the API accepts only tokens for its audience.

## Exceptions, on purpose

| Secret | Why it exists | Where it lives |
|---|---|---|
| MCP server API key | Other people's agents call the remote MCP server and can't get an Entra token for our tenant | Key Vault (`mcp-api-key`), read by the managed identity |
| APIM subscription key | The `/api` product in API Management, for external API consumers | API Management |
| Redis access key | APIM's external cache connects with a key (only if the semantic cache is on, ADR 0002) | The API Management cache entity, read with `listKeys()` at deploy time |

The Application Insights connection string is not a secret (it only allows sending
telemetry) and is a plain setting.

## Alternatives considered

| Option | Why not |
|---|---|
| Keys in Key Vault, read at start-up | Still keys: they can leak from memory dumps or logs, need rotation, and give full access |
| System-assigned identities per app | The identity exists only after the app is created, so roles can't be granted before the first start; three apps would need three sets of roles |
| A service principal with a client secret | A secret again, with an expiry date |

## Consequences

- Roles take a few minutes to propagate: a fresh deployment may see `Forbidden` for a
  short while.
- A lost role is an outage, and it's silent until called: the incident drill (L5) removed
  the Search role and every search failed. The fix is re-applying the template
  (`deploy.sh --reapply-infra`), which recreates every role assignment.
- Cosmos DB's data roles are its own (`sqlRoleAssignments`), not Azure RBAC.
- Local development needs `az login` and the developer's own data roles; scripts that
  need keys don't exist.
- `scripts/check_isolation.py` (L4) proves the other side: from the internet, every service
  still requires an Entra token.
