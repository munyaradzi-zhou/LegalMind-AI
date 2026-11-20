"""Are the data services refusing the internet? Call each one from this machine and report.

Every request carries a valid Entra ID token for the signed-in operator (``az login``), so
a refusal is the network rule, not missing credentials: each service says why in its error
body ("public network access is disabled", "not from an approved private link", ...).
Run it against an isolated environment (all refused) and against dev (all reachable).

    python scripts/check_isolation.py --resource-group rg-legalmind-staging
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from dataclasses import dataclass
from email.utils import formatdate

import httpx

# Phrases the services use when the network, not the caller, is the reason for a refusal
NETWORK_REFUSALS = (
    "public network access",
    "publicnetworkaccess",
    "private endpoint",
    "private link",
    "virtual network",
    "not authorized to perform this operation from",
    "forbiddenbyconnection",
    "ip address",
    "firewall",
    "requests originating from",
    "is not allowed",
    # Storage answers a refused network with AuthorizationFailure; a missing data role
    # would be AuthorizationPermissionMismatch instead
    "<code>authorizationfailure</code>",
)

# A real data-plane call per Cognitive Services kind: the root path answers 200 even when
# public access is disabled, because it isn't subject to the network rule.
LANGUAGE_CALL = (
    "/language/:analyze-text?api-version=2023-04-01",
    {"kind": "LanguageDetection", "analysisInput": {"documents": [{"id": "1", "text": "hallo"}]}},
)
COGNITIVE_CALLS: dict[str, tuple[str, dict[str, object] | None]] = {
    "OpenAI": ("/openai/models?api-version=2024-10-21", None),
    "FormRecognizer": ("/documentintelligence/documentModels?api-version=2024-11-30", None),
    "ContentSafety": ("/contentsafety/text:analyze?api-version=2024-09-01", {"text": "hallo"}),
    "CognitiveServices": LANGUAGE_CALL,
    "AIServices": LANGUAGE_CALL,
}


@dataclass
class Target:
    service: str
    url: str
    scope: str
    body: dict[str, object] | None = None  # POST this instead of a GET


def _az(*args: str) -> str:
    az = shutil.which("az") or "az"  # az.cmd on Windows
    return subprocess.run([az, *args], check=True, capture_output=True, text=True).stdout  # noqa: S603 - fixed args


def _targets(resource_group: str) -> list[Target]:
    query = "[].{n:name,t:type,k:kind}"
    resources = json.loads(_az("resource", "list", "-g", resource_group, "--query", query, "-o", "json"))
    targets: list[Target] = []
    for r in resources:
        name, kind, rtype = r["n"], (r["k"] or ""), r["t"]
        if rtype == "Microsoft.CognitiveServices/accounts":
            host = "openai.azure.com" if kind == "OpenAI" else "cognitiveservices.azure.com"
            path, body = COGNITIVE_CALLS.get(kind, LANGUAGE_CALL)
            scope = "https://cognitiveservices.azure.com/.default"
            targets.append(Target(f"{kind} {name}", f"https://{name}.{host}{path}", scope, body))
        elif rtype == "Microsoft.Search/searchServices":
            url = f"https://{name}.search.windows.net/indexes?api-version=2024-07-01"
            targets.append(Target(f"AI Search {name}", url, "https://search.azure.com/.default"))
        elif rtype == "Microsoft.Storage/storageAccounts":
            url = f"https://{name}.blob.core.windows.net/?comp=list"
            targets.append(Target(f"Storage {name}", url, "https://storage.azure.com/.default"))
        elif rtype == "Microsoft.KeyVault/vaults":
            url = f"https://{name}.vault.azure.net/secrets?api-version=7.4"
            targets.append(Target(f"Key Vault {name}", url, "https://vault.azure.net/.default"))
        elif rtype == "Microsoft.DocumentDB/databaseAccounts":
            url = f"https://{name}.documents.azure.com/dbs"
            targets.append(Target(f"Cosmos DB {name}", url, f"https://{name}.documents.azure.com/.default"))
    return targets


def _token(scope: str) -> str:
    return _az("account", "get-access-token", "--scope", scope, "--query", "accessToken", "-o", "tsv").strip()


def check(target: Target) -> tuple[str, int | None, str]:
    headers = {"Authorization": f"Bearer {_token(target.scope)}", "x-ms-version": "2023-11-03"}
    if "documents.azure.com" in target.url:
        # Cosmos data plane: AAD tokens go in a type=aad authorization header
        headers = {
            "Authorization": f"type=aad&ver=1.0&sig={_token(target.scope)}",
            "x-ms-version": "2018-12-31",
            "x-ms-date": formatdate(usegmt=True),
        }
    try:
        if target.body is None:
            response = httpx.get(target.url, headers=headers, timeout=20)
        else:
            response = httpx.post(target.url, headers=headers, json=target.body, timeout=20)
    except httpx.HTTPError as error:
        return "refused (no connection)", None, type(error).__name__
    body = response.text[:400].replace("\n", " ").replace("﻿", "")  # some services start with a BOM
    if response.status_code < 400:
        return "reachable", response.status_code, ""
    if any(p in body.lower() for p in NETWORK_REFUSALS):
        return "refused by network rule", response.status_code, body[:160]
    return "reachable (refused for another reason)", response.status_code, body[:160]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--resource-group", required=True)
    parser.add_argument("--json", help="write the results to this file")
    args = parser.parse_args()
    try:
        import truststore  # the OS certificate store: works behind TLS-inspecting proxies

        truststore.inject_into_ssl()
    except ImportError:
        pass
    rows = []
    for target in _targets(args.resource_group):
        verdict, status, detail = check(target)
        rows.append({"service": target.service, "verdict": verdict, "status": status, "detail": detail})
        print(f"{target.service:58} {verdict:40} {status or '-':>4}  {detail[:90]}", flush=True)
    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(rows, f, indent=2)


if __name__ == "__main__":
    main()
