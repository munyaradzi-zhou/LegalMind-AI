# ADR 0008: Network isolation as a deployment parameter

- Status: accepted
- Date: 2026-11-21 (decided 20 November, L4)

## Context

With no keys (ADR 0007), every service already needs an Entra token. But it still listens
on the internet: a leaked token, or a misconfigured role, is usable from anywhere. A law
firm's security review will ask for private networking. Private endpoints cost money for
every service, every hour, and complicate development (a laptop can't reach them).

## Decision

One Bicep parameter, **`networkIsolation`** (default on for prod, off for dev):

- **On:** a VNet with a subnet for the Container Apps environment and one for private
  endpoints; a private endpoint and private DNS zone for each data service (OpenAI, AI
  Search, Language, Document Intelligence, Content Understanding, Content Safety, Cosmos DB,
  Blob Storage, Key Vault: 9); **public network access disabled** on all of them.
- **Off:** the same services on their public endpoints, still Entra-only.

The rest of the template is the same, so dev and prod differ by a parameter, not by a
separate template.

## Evidence (20–21 Nov, `notes/results/2026-11-21-isolation/`)

- `scripts/check_isolation.py`, with a valid token, from the internet:
  - dev (off): **10/10** services answered;
  - an isolated copy (staging): **9/9 refused** by the network rule ("Public access is
    disabled", "Request originated from IP … through public internet", "source is not
    allowed").
- From inside the VNet (the API image as a container instance in the private-endpoint
  network): names resolved to private IPs (10.60.2.x), a document was ingested and a
  question answered with a grounded, cited answer, and Cosmos DB stored only redacted text.
- A gotcha found on the way: Cognitive Services answer 200 on `/` even with public access
  disabled; only real API paths are subject to the network rule, so the check calls those.

## Alternatives considered

| Option | Why not |
|---|---|
| Always isolated | About €60 a month more for 9 private endpoints in dev (list price about €0.22 per endpoint per day), and no local development against Azure |
| Never isolated (identity only) | Fine for dev and demos; not what a regulated client accepts in production |
| Service endpoints / IP firewall rules | Cheaper, but traffic still goes to public endpoints and IP lists break with Container Apps' outbound addresses |
| A separate isolated template | Two templates drift apart; one parameter keeps them the same |

## Consequences

- Not tested live end to end: the trial subscription allows one Container Apps environment,
  so the VNet-integrated environment in Bicep was proven with a container instance in the
  VNet, not with the Container Apps environment itself.
- With isolation on, the AI gateway (ADR 0002) is off: a Developer or Basic v2 API
  Management can't reach private endpoints; production would need Standard v2 with VNet
  integration or Premium.
- Key Vault refuses the deploying machine when isolated: the MCP key must be created from
  inside the VNet.
- The trial also allows one Azure OpenAI account per region, so the isolated copy ran its
  model in Sweden Central (`openAiLocation`).
