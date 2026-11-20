"""Set the access-control fields on chunks indexed before document-level security existed.

Chunks without ``allowed_groups`` are invisible to everyone once trimming is on. For each
corpus document (``corpus/manifest.json``) this sets ``matter``, ``allowed_groups`` and
``privileged`` on its chunks in place (a merge: no re-analysis, no new embeddings), then
lists any chunks still without access: uploads that aren't part of the corpus, which stay
hidden until they are re-ingested with a matter.

Run it again after changing MATTER_GROUPS, so the chunks carry the matters' Entra groups.

Usage::

    python scripts/backfill_access.py            # as the operator (az login)
    python scripts/backfill_access.py --dry-run
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "corpus" / "manifest.json"


async def backfill(dry_run: bool) -> int:
    from legalmind.access import DocumentAccess
    from legalmind.ingestion.indexing import access_fields
    from legalmind.services.search_client import SearchService

    search = SearchService()
    documents = json.loads(MANIFEST.read_text(encoding="utf-8"))["documents"]
    updated = 0
    for doc in documents:
        access = DocumentAccess(matter=doc.get("matter"), privileged=doc.get("privileged", False))
        fields = access_fields(
            {"matter": access.matter, "privileged": access.privileged, "allowed_groups": access.allowed_groups()}
        )
        title = doc["title"].replace("'", "''")
        ids = await search.find_ids(f"title eq '{title}'")
        print(f"{doc['id']:34} {len(ids):4} chunks -> {', '.join(fields['allowed_groups'])}"
              f"{' (privileged)' if fields['privileged'] else ''}")  # fmt: skip
        if ids and not dry_run:
            for start in range(0, len(ids), 500):
                batch = [{"id": i, **fields} for i in ids[start : start + 500]]
                updated += await search.merge_documents(batch)

    if not dry_run:
        await asyncio.sleep(2)  # merges become searchable after a moment
    hidden = await search.find_ids("not allowed_groups/any()")
    print(f"\n{updated} chunks updated; {len(hidden)} chunks without access (visible to nobody)")
    return len(hidden)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true", help="show what would change")
    args = parser.parse_args()
    try:
        import truststore

        truststore.inject_into_ssl()
    except ImportError:
        pass
    asyncio.run(backfill(args.dry_run))


if __name__ == "__main__":
    main()
