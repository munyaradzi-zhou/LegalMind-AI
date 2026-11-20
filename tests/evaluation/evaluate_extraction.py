"""Document extraction quality: how well does each backend read what a law firm receives?

The fictional case files in the corpus are rendered from known text (corpus/sources/*.md),
so every one of them has exact ground truth: the reading text, every table cell and every
checkbox state. Scans and photos also have a clean twin (the same page as a born-digital
PDF), which isolates what the degradation costs.

Backends:
- ``layout``: Document Intelligence ``prebuilt-layout`` with Markdown output (what ingestion uses)
- ``read``: Document Intelligence ``prebuilt-read``, plain OCR text, about a seventh of the price
- ``content_understanding``: Azure Content Understanding with LegalMind's analyzers

Metrics per document and backend: character error rate (CER) and word error rate (WER) of
the reading text, table cell accuracy, checkbox accuracy, latency.

Usage::

    python scripts/build_corpus.py      # first
    python -m tests.evaluation.evaluate_extraction
    python -m tests.evaluation.evaluate_extraction --backends layout read
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import time
from pathlib import Path
from typing import Any

from rapidfuzz.distance import Levenshtein

from tests.evaluation._common import markdown_table, mean, write_report

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "corpus" / "manifest.json"

# Price per 1,000 pages, East US 2, November 2026 (list prices)
PRICE_PER_1000_PAGES = {"layout": 10.0, "read": 1.5}

_CHECK_TOKEN = re.compile(r"☒|☐|:selected:|:unselected:")
_TABLE_SEPARATOR = re.compile(r"^\|?\s*:?-{3,}")


# ─── Text normalisation and scoring ──────────────────────────────────────────


def reading_text(text: str) -> str:
    """Reduce Markdown or OCR output to plain reading text for CER/WER."""
    lines = []
    for raw in text.splitlines():
        line = raw.strip()
        if _TABLE_SEPARATOR.match(line):
            continue
        line = _CHECK_TOKEN.sub(" ", line)
        line = re.sub(r"^#{1,6}\s+", "", line)
        line = re.sub(r"^[-*]\s+", "", line)
        line = line.replace("|", " ").replace("**", "")
        lines.append(line)
    return " ".join(" ".join(lines).split())


def error_rates(truth: str, extracted: str) -> tuple[float, float]:
    """Character and word error rate (edit distance / length of the truth)."""
    t, e = reading_text(truth), reading_text(extracted)
    cer = Levenshtein.distance(t, e) / max(1, len(t))
    tw, ew = t.split(), e.split()
    wer = Levenshtein.distance(tw, ew) / max(1, len(tw))
    return cer, wer


def markdown_tables(text: str) -> list[list[list[str]]]:
    """Pipe tables in Markdown text, as rows of cells."""
    tables: list[list[list[str]]] = []
    current: list[list[str]] = []
    for raw in [*text.splitlines(), ""]:
        line = raw.strip()
        if line.startswith("|"):
            if not _TABLE_SEPARATOR.match(line):
                current.append([c.strip() for c in re.split(r"(?<!\\)\|", line.strip("|"))])
        elif current:
            tables.append(current)
            current = []
    return tables


def _cell(text: str) -> str:
    return " ".join(text.replace("\\|", "|").replace("**", "").lower().split())


def table_accuracy(truth: list[list[list[str]]], extracted: list[list[list[str]]]) -> tuple[int, int]:
    """Ground-truth cells found at the same row and column in the best-matching extracted table."""
    correct = total = 0
    for table in truth:
        cells = [(r, c, _cell(v)) for r, row in enumerate(table) for c, v in enumerate(row)]
        total += len(cells)
        best = 0
        for candidate in extracted:
            hits = sum(
                1 for r, c, v in cells if r < len(candidate) and c < len(candidate[r]) and _cell(candidate[r][c]) == v
            )
            best = max(best, hits)
        correct += best
    return correct, total


def checkbox_accuracy(truth: list[dict[str, Any]], extracted: str) -> tuple[int, int] | None:
    """Checkbox states in reading order compared with the ground truth."""
    if not truth:
        return None
    states = [token in ("☒", ":selected:") for token in _CHECK_TOKEN.findall(extracted)]
    expected = [c["checked"] for c in truth]
    correct = sum(1 for i, value in enumerate(expected) if i < len(states) and states[i] == value)
    return correct, len(expected)


# ─── Backends ────────────────────────────────────────────────────────────────


async def run_layout(data: bytes, _: dict[str, Any]) -> tuple[str, int]:
    from legalmind.ingestion.document_intelligence import DocumentIntelligenceAnalyzer

    result = await DocumentIntelligenceAnalyzer().analyze(data)
    return result.content, result.page_count


async def run_read(data: bytes, _: dict[str, Any]) -> tuple[str, int]:
    from legalmind.ingestion.document_intelligence import DocumentIntelligenceAnalyzer

    result = await DocumentIntelligenceAnalyzer().analyze(data, model_id="prebuilt-read")
    return result.content, result.page_count


_cu_ready = False


async def run_content_understanding(data: bytes, doc: dict[str, Any]) -> tuple[str, int]:
    from legalmind.ingestion.content_understanding import ContentUnderstandingClient, LegalDocumentType
    from legalmind.ingestion.document_intelligence import normalize_markdown

    global _cu_ready
    client = ContentUnderstandingClient()
    if not _cu_ready:
        await client.ensure_analyzers()
        _cu_ready = True
    try:
        doc_type = LegalDocumentType(doc["document_type"])
    except ValueError:
        doc_type = LegalDocumentType.GENERAL
    result = await client.analyze_document(data, document_type=doc_type, content_type=doc["content_type"])
    content, _ = normalize_markdown(result.get("content", ""), [])
    return content, len(result.get("pages", []))


BACKENDS = {"layout": run_layout, "read": run_read, "content_understanding": run_content_understanding}

CONTENT_TYPES = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".jpg": "image/jpeg",
}


# ─── Run ─────────────────────────────────────────────────────────────────────


def cases() -> list[dict[str, Any]]:
    """Every fictional document as delivered, plus the clean twin of each scan and photo."""
    documents = json.loads(MANIFEST.read_text(encoding="utf-8"))["documents"]
    out = []
    for doc in documents:
        if doc["kind"] != "case_file" or not doc.get("ground_truth"):
            continue
        variants = [("delivered", doc["file"])]
        if doc["format"] in ("scan", "photo") and doc.get("clean_pdf"):
            variants.append(("clean twin", doc["clean_pdf"]))
        for variant, file in variants:
            path = ROOT / file
            out.append(
                {
                    **doc,
                    "variant": variant,
                    "path": path,
                    "content_type": CONTENT_TYPES[path.suffix.lower()],
                    "truth_text": (ROOT / doc["ground_truth"]).read_text(encoding="utf-8"),
                    "truth": json.loads((ROOT / doc["ground_truth"]).with_suffix(".json").read_text(encoding="utf-8")),
                }
            )
    return out


async def evaluate(backends: list[str]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for case in cases():
        data = case["path"].read_bytes()
        for backend in backends:
            started = time.monotonic()
            try:
                content, pages = await BACKENDS[backend](data, case)
            except Exception as error:
                rows.append(
                    {
                        "document": case["id"],
                        "variant": case["variant"],
                        "format": case["format"],
                        "backend": backend,
                        "error": f"{type(error).__name__}: {str(error)[:160]}",
                    }
                )
                print(f"{backend:22} {case['id']:38} {case['variant']:10} ERROR {type(error).__name__}", flush=True)  # noqa: T201
                continue
            seconds = time.monotonic() - started
            cer, wer = error_rates(case["truth_text"], content)
            cells_ok, cells = table_accuracy(case["truth"]["tables"], markdown_tables(content))
            checks = checkbox_accuracy(case["truth"]["selection_marks"], content)
            row = {
                "document": case["id"],
                "variant": case["variant"],
                "format": case["format"] if case["variant"] == "delivered" else "pdf",
                "backend": backend,
                "pages": pages,
                "cer": round(cer, 4),
                "wer": round(wer, 4),
                "table_cells_correct": cells_ok,
                "table_cells": cells,
                "checkboxes_correct": checks[0] if checks else None,
                "checkboxes": checks[1] if checks else None,
                "seconds": round(seconds, 1),
            }
            rows.append(row)
            print(  # noqa: T201
                f"{backend:22} {case['id']:38} {case['variant']:10} CER {cer:.3f} WER {wer:.3f} "
                f"tables {cells_ok}/{cells} {seconds:.1f}s",
                flush=True,
            )
    return rows


def _cells(row: dict[str, Any]) -> str:
    return f"{row['table_cells_correct']}/{row['table_cells']}"


def summarise(rows: list[dict[str, Any]], backends: list[str]) -> tuple[dict[str, Any], str]:
    ok = [r for r in rows if "error" not in r]
    summary: dict[str, Any] = {}
    by_backend = []
    for backend in backends:
        mine = [r for r in ok if r["backend"] == backend]
        errors = sum(1 for r in rows if r["backend"] == backend and "error" in r)
        delivered = [r for r in mine if r["variant"] == "delivered"]
        cells = sum(r["table_cells"] for r in delivered)
        checks = sum(r["checkboxes"] or 0 for r in delivered)
        entry = {
            "backend": backend,
            "documents": len(delivered),
            "errors": errors,
            "CER": mean([r["cer"] for r in delivered]),
            "WER": mean([r["wer"] for r in delivered]),
            "table cells": (sum(r["table_cells_correct"] for r in delivered) / cells) if cells else None,
            "checkboxes": (sum(r["checkboxes_correct"] or 0 for r in delivered) / checks) if checks else None,
            "seconds/doc": mean([r["seconds"] for r in delivered]),
            "$/1000 pages": PRICE_PER_1000_PAGES.get(backend, "-"),
        }
        summary[backend] = entry
        by_backend.append(entry)

    by_format = []
    for backend in backends:
        for fmt in ("pdf", "docx", "scan", "photo"):
            mine = [r for r in ok if r["backend"] == backend and r["format"] == fmt and r["variant"] == "delivered"]
            if mine:
                cells = sum(r["table_cells"] for r in mine)
                by_format.append(
                    {
                        "backend": backend,
                        "format": fmt,
                        "documents": len(mine),
                        "CER": mean([r["cer"] for r in mine]),
                        "WER": mean([r["wer"] for r in mine]),
                        "table cells": (sum(r["table_cells_correct"] for r in mine) / cells) if cells else "-",
                    }
                )

    degradation = []
    for backend in backends:
        for doc in sorted({r["document"] for r in ok if r["variant"] == "clean twin" and r["backend"] == backend}):
            pair = {r["variant"]: r for r in ok if r["backend"] == backend and r["document"] == doc}
            if len(pair) == 2:
                degradation.append(
                    {
                        "backend": backend,
                        "document": doc,
                        "format": pair["delivered"]["format"],
                        "CER clean": pair["clean twin"]["cer"],
                        "CER delivered": pair["delivered"]["cer"],
                        "cells clean": _cells(pair["clean twin"]),
                        "cells delivered": _cells(pair["delivered"]),
                    }
                )

    errors = [r for r in rows if "error" in r]
    markdown = (
        "# Extraction benchmark\n\n"
        f"{len({r['document'] for r in rows})} fictional case-file documents with exact ground truth "
        "(text, table cells, checkbox states), as delivered (PDF, Word, scan, photo) and, for scans and photos, "
        "as a clean born-digital twin.\n\n"
        "## By backend (documents as delivered)\n\n"
        + markdown_table(by_backend)
        + "\n## By format\n\n"
        + markdown_table(by_format)
        + "\n## What the scan or photo costs (same page, clean vs delivered)\n\n"
        + markdown_table(degradation)
        + "\n## Per document\n\n"
        + markdown_table([{k: v for k, v in r.items() if k != "error"} for r in ok])
        + ("\n## Errors\n\n" + markdown_table(errors) if errors else "")
    )
    return summary, markdown


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--backends", nargs="*", default=list(BACKENDS), choices=list(BACKENDS))
    args = parser.parse_args()
    try:
        import truststore

        truststore.inject_into_ssl()
    except ImportError:
        pass
    rows = asyncio.run(evaluate(args.backends))
    summary, markdown = summarise(rows, args.backends)
    write_report("extraction", summary, rows, markdown)


if __name__ == "__main__":
    main()
