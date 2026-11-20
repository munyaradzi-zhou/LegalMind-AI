# ADR 0001: Document-level security trimming with a group filter field

- Status: accepted
- Date: 2026-11-20

## Context

LegalMind holds two kinds of documents: public law (legislation, published rulings) and a
law firm's client files, grouped by matter (case). A lawyer may only see the files of the
matters they work on, and paralegals may not see documents covered by legal professional
privilege. A retrieval-augmented assistant must enforce this in retrieval: once a passage
reaches the model's context it can show up in the answer, a citation or an excerpt.

Users sign in with Entra ID. The API's app registration defines app roles (`Lawyer`,
`Paralegal`, `Admin`, `Service`) and each matter has a security group.

Azure AI Search offers two ways to trim results:

1. **A filterable field plus a filter on every query** (the long-standing pattern): each
   chunk carries the groups that may see it; the application adds
   `allowed_groups/any(g: search.in(g, '<the user's groups>'))`.
2. **Built-in document-level access control** (preview, API `2025-05-01-preview` and later):
   the index stores Entra user and group IDs in `permissionFilter` fields and the service
   trims results itself from the user's token (`x-ms-query-source-authorization`).

## Decision

Use option 1, enforced in one place: `HybridSearchEngine` builds the filter from the
request's principal (`legalmind.access`) and refuses to search without one.

- Chunks carry `matter`, `allowed_groups` (hidden: filterable, never returned) and
  `privileged`. Public law carries `public`; a client file carries its matter's group.
- The principal comes from the token: `oid`, `roles`, `groups`. Privileged chunks are
  filtered out unless the user has the `Lawyer` role.
- Ingestion needs the `Admin` role and an explicit matter (`public` for public law). Code
  that forgets to pass access produces chunks visible to nobody, not to everybody.
- Chat sessions belong to the user who created them: another user's session ID answers 404.
- The MCP server calls the API with its own managed identity (app role `Service`, no
  groups), so agents using it reach public law only.

## Why not the built-in preview

- **Preview**: no SLA, and the API version and SDK support are still moving. A security
  boundary should not depend on preview behaviour.
- **One rule for roles and groups**: "paralegals don't see privileged documents" is a role
  rule; the built-in feature trims on user and group IDs only, so the role rule would need
  a filter field anyway.
- **Testable without Entra**: the filter is a string the unit tests check and a fake index
  evaluates; the leakage tests and the access evaluation run in CI and locally with test
  users, without real accounts.
- **Callers without a user**: the evaluation gate and the MCP server search as applications;
  with a filter field they get exactly the access we give them.

What we give up: Search does not enforce the rule itself, so a code path that queries the
index without `HybridSearchEngine` would bypass it. Mitigations: the engine is the only
search path in the code, it fails closed, and tests cover chat, `/api/search`, the agents'
search tool and the MCP tools. Revisit when the built-in feature is generally available.

## Consequences

- Group membership changes apply on the user's next token (up to an hour).
- A user in more than 200 groups gets no `groups` claim (overage); they then see public law
  only, rather than the API calling Microsoft Graph on every request.
- Moving a document to another matter means updating its chunks' `allowed_groups`
  (`scripts/backfill_access.py` does this from the corpus manifest).
- The sign-in token's groups are object IDs; `MATTER_GROUPS` maps matter numbers to them.
  A matter without a mapping gets a tag no token carries, so its files stay hidden.
