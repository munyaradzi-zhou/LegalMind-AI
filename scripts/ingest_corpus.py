"""Load the evaluation corpus (``corpus/manifest.json``) into Azure through the ingestion pipeline.

Runs the same pipeline as ``POST /api/ingest`` in-process, as the signed-in operator
(``az login``), so a bulk load doesn't depend on the deployed API's version or timeouts.
Each document keeps its manifest metadata; legislation and case law get their public
source as ``source_url``. Re-running replaces earlier versions of the same title.

Usage::

    python scripts/build_corpus.py                 # first
    python scripts/ingest_corpus.py                # everything
    python scripts/ingest_corpus.py --only wet-bw7-huur huur-02-gebrekenbrief
    python scripts/ingest_corpus.py --report corpus/build/ingestion.json

The Azure endpoints come from the environment, as for the API (see ``.env.example``).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "corpus" / "manifest.json"

CONTENT_TYPES = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".txt": "text/plain",
}


async def ingest(documents: list[dict[str, Any]], concurrency: int) -> list[dict[str, Any]]:
    from legalmind.ingestion.pipeline import IngestionPipeline

    pipeline = IngestionPipeline()
    semaphore = asyncio.Semaphore(concurrency)
    results: list[dict[str, Any]] = []

    async def one(doc: dict[str, Any]) -> None:
        path = ROOT / doc["file"]
        metadata = {"corpus_id": doc["id"], "kind": doc["kind"], "matter": doc.get("matter") or ""}
        if doc["kind"] in ("legislation", "case_law"):
            metadata["source_url"] = doc["source"]
        async with semaphore:
            started = time.monotonic()
            try:
                result = await pipeline.ingest(
                    path.read_bytes(),
                    filename=path.name,
                    content_type=CONTENT_TYPES[path.suffix.lower()],
                    document_type=doc["document_type"],
                    title=doc["title"],
                    metadata=metadata,
                    replace_existing=True,
                )
            except Exception as error:  # report and continue with the next document
                results.append({"id": doc["id"], "ok": False, "error": f"{type(error).__name__}: {error}"})
                print(f"FAILED {doc['id']}: {type(error).__name__}: {error}", flush=True)
                return
        row = {
            "id": doc["id"],
            "ok": result.is_success,
            "format": doc["format"],
            "pages": result.page_count,
            "chunks": result.chunk_count,
            "indexed": result.chunks_indexed,
            "failed": result.chunks_failed,
            "replaced": result.metadata.get("chunks_replaced", 0),
            "mean_word_confidence": result.metadata.get("mean_word_confidence"),
            "seconds": round(time.monotonic() - started, 1),
        }
        results.append(row)
        print(
            f"{'ok' if row['ok'] else 'FAILED':6} {doc['id']:38} {doc['format']:5} pages={row['pages']:<3} "
            f"chunks={row['chunks']:<4} conf={row['mean_word_confidence']} {row['seconds']}s",
            flush=True,
        )

    await asyncio.gather(*(one(d) for d in documents))
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--only", nargs="*", help="corpus ids to ingest (default: all)")
    parser.add_argument("--concurrency", type=int, default=3)
    parser.add_argument("--report", type=Path, help="write the per-document results as JSON")
    args = parser.parse_args()

    try:
        import truststore

        truststore.inject_into_ssl()
    except ImportError:
        pass

    documents = json.loads(MANIFEST.read_text(encoding="utf-8"))["documents"]
    if args.only:
        documents = [d for d in documents if d["id"] in set(args.only)]
    results = asyncio.run(ingest(documents, args.concurrency))

    ok = sum(1 for r in results if r["ok"])
    print(f"\n{ok}/{len(results)} documents ingested, {sum(r.get('indexed', 0) for r in results)} chunks indexed")
    if args.report:
        args.report.write_text(json.dumps(sorted(results, key=lambda r: r["id"]), indent=2) + "\n", encoding="utf-8")
    if ok < len(results):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
