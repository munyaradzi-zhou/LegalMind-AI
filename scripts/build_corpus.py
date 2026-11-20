"""Build the LegalMind evaluation corpus.

Three kinds of documents, in the formats a law firm actually receives:

- **Legislation**: Burgerlijk Wetboek Boek 7, titles on tenancy, employment and insurance,
  from the official BWB repository (XML, pinned to one version), rendered to PDF.
- **Case law**: rulings from the open data service of de Rechtspraak (XML, pinned by ECLI),
  rendered to PDF.
- **Case files**: fictional matters written for this project (``corpus/sources/*.md``),
  rendered as born-digital PDF, Word, a degraded scan (image-only PDF) or a phone photo (JPEG).
  Because these are rendered from known text, they double as OCR ground truth.

Legislation and court decisions are not protected by copyright in the Netherlands
(Auteurswet, article 11). The case files are fictional; any resemblance is coincidental.

Usage::

    python scripts/build_corpus.py            # build everything into corpus/build/
    python scripts/build_corpus.py --offline  # reuse downloaded XML from corpus/build/raw/
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import random
import re
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import numpy as np
import pypdfium2 as pdfium
from docx import Document
from docx.shared import Pt
from fpdf import FPDF
from fpdf.enums import XPos, YPos
from lxml import etree
from PIL import Image, ImageFilter

ROOT = Path(__file__).resolve().parents[1]
SOURCES = ROOT / "corpus" / "sources"
BUILD = ROOT / "corpus" / "build"
RAW = BUILD / "raw"
FILES = BUILD / "files"
TRUTH = BUILD / "groundtruth"
MANIFEST = ROOT / "corpus" / "manifest.json"

FONT_DIR = Path("C:/Windows/Fonts") if sys.platform == "win32" else Path("/usr/share/fonts/truetype/msttcorefonts")
FIXED_DATE = datetime(2026, 11, 20, tzinfo=UTC)
USER_AGENT = "LegalMind-corpus-builder/1.0 (+https://github.com/munyaradzi-zhou/LegalMind-AI)"

# Burgerlijk Wetboek Boek 7, version in force from 1 January 2026
BWB_ID = "BWBR0005290"
BWB_VERSION = "2026-01-01_1"
BWB_URL = (
    f"https://repository.officiele-overheidspublicaties.nl/bwb/{BWB_ID}/{BWB_VERSION}/xml/{BWB_ID}_{BWB_VERSION}.xml"
)

LEGISLATION = [
    # (id, title, BWB path prefixes)
    (
        "wet-bw7-huur",
        "Burgerlijk Wetboek Boek 7, Titel 4 Huur, afdelingen 1 tot en met 5 (huur en huur van woonruimte)",
        [f"/Boek7/Titeldeel4/Afdeling{n}/" for n in range(1, 6)],
    ),
    (
        "wet-bw7-arbeid",
        "Burgerlijk Wetboek Boek 7, Titel 10 Arbeidsovereenkomst",
        ["/Boek7/Titeldeel10/"],
    ),
    (
        "wet-bw7-verzekering",
        "Burgerlijk Wetboek Boek 7, Titel 17 Verzekering, afdeling 1 en 2 (algemeen en schadeverzekering)",
        ["/Boek7/Titeldeel17/Afdeling1/", "/Boek7/Titeldeel17/Afdeling2/"],
    ),
]

RULINGS = [
    # (ECLI, theme)
    ("ECLI:NL:RBZWB:2025:203", "huur"),
    ("ECLI:NL:RBNHO:2025:422", "huur"),
    ("ECLI:NL:RBMNE:2025:303", "arbeid"),
    ("ECLI:NL:RBZWB:2025:321", "arbeid"),
    ("ECLI:NL:RBROT:2025:1815", "arbeid"),
    ("ECLI:NL:RBROT:2023:1772", "verzekering"),
]
RS = "{http://www.rechtspraak.nl/schema/rechtspraak-1.0}"

# The three text files the project started with
SAMPLES = [
    ("sample-case-summary", "sample_data/case_files/sample_case_summary_nl.txt", "case_file", "VRB/2024/ZK-9012"),
    ("sample-insurance-policy", "sample_data/contracts/sample_insurance_policy_nl.txt", "contract", "VRB/2024/ZK-9012"),
    (
        "sample-legal-letter",
        "sample_data/correspondence/sample_legal_letter_nl.txt",
        "correspondence",
        "VRB/2024/ZK-9012",
    ),
]


# ─── A small document model shared by all renderers ──────────────────────────


@dataclass
class Block:
    kind: str  # heading | para | bullet | check | table
    text: str = ""
    level: int = 0
    checked: bool = False
    rows: list[list[str]] = field(default_factory=list)


def parse_markdown(text: str) -> tuple[dict[str, str], list[Block]]:
    """Parse the small Markdown dialect of ``corpus/sources``: front matter, #/##, -, - [x], tables."""
    meta: dict[str, str] = {}
    body = text
    if text.startswith("---"):
        _, front, body = text.split("---", 2)
        for line in front.strip().splitlines():
            key, _, value = line.partition(":")
            meta[key.strip()] = value.strip()

    blocks: list[Block] = []
    table: list[list[str]] = []
    para: list[str] = []

    def flush() -> None:
        if para:
            blocks.append(Block("para", " ".join(para)))
            para.clear()
        if table:
            blocks.append(Block("table", rows=[r for r in table if not all(set(c) <= {"-", " "} for c in r)]))
            table.clear()

    for raw in body.strip().splitlines():
        line = raw.rstrip()
        if not line.strip():
            flush()
            continue
        if line.startswith("|"):
            if para:
                flush()
            table.append([c.strip() for c in line.strip("|").split("|")])
            continue
        if table:
            flush()
        if heading := re.match(r"^(#{1,3})\s+(.*)", line):
            flush()
            blocks.append(Block("heading", heading.group(2), level=len(heading.group(1))))
        elif check := re.match(r"^- \[([ xX])\]\s+(.*)", line):
            flush()
            blocks.append(Block("check", check.group(2), checked=check.group(1).lower() == "x"))
        elif line.startswith("- "):
            flush()
            blocks.append(Block("bullet", line[2:]))
        elif re.match(r"^\d+\.\s", line) and not para:
            blocks.append(Block("para", line))
        else:
            # Letter-style lines (addresses) stay on their own line
            para.append(line)
            if len(line) < 60 and not line.endswith((".", ",", ":", ";")):
                flush()
    flush()
    return meta, blocks


def plain_text(blocks: list[Block]) -> str:
    """Ground-truth reading text of a document, one block per line."""
    lines: list[str] = []
    for b in blocks:
        if b.kind == "table":
            lines.extend(" ".join(row) for row in b.rows)
        else:
            lines.append(b.text.replace("**", ""))
    return "\n".join(lines) + "\n"


# ─── Renderers ───────────────────────────────────────────────────────────────


class _Pdf(FPDF):
    footer_text = ""

    def footer(self) -> None:
        if self.footer_text:
            self.set_y(-12)
            self.set_font("Body", "", 8)
            self.cell(0, 6, f"{self.footer_text} - pagina {self.page_no()}", align="C")


def render_pdf(blocks: list[Block], path: Path, *, footer: str = "") -> None:
    pdf = _Pdf(format="A4")
    pdf.footer_text = footer
    pdf.set_creation_date(FIXED_DATE)
    pdf.add_font("Body", "", str(FONT_DIR / "arial.ttf"))
    pdf.add_font("Body", "B", str(FONT_DIR / "arialbd.ttf"))
    pdf.set_margins(22, 20, 22)
    pdf.set_auto_page_break(True, margin=18)
    pdf.add_page()
    for b in blocks:
        if b.kind == "heading":
            pdf.ln(2)
            pdf.set_font("Body", "B", {1: 15, 2: 12, 3: 11}[b.level])
            pdf.multi_cell(0, 7, b.text, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
            pdf.ln(1)
        elif b.kind == "para":
            pdf.set_font("Body", "", 10.5)
            pdf.multi_cell(0, 5.4, b.text, markdown=True, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
            pdf.ln(2.2)
        elif b.kind == "bullet":
            pdf.set_font("Body", "", 10.5)
            pdf.set_x(pdf.l_margin + 4)
            pdf.multi_cell(0, 5.4, "- " + b.text, markdown=True, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
            pdf.ln(1)
        elif b.kind == "check":
            pdf.set_font("Body", "", 10.5)
            x, y = pdf.l_margin + 2, pdf.get_y() + 0.8
            pdf.rect(x, y, 3.8, 3.8)
            if b.checked:
                pdf.line(x + 0.5, y + 0.5, x + 3.3, y + 3.3)
                pdf.line(x + 0.5, y + 3.3, x + 3.3, y + 0.5)
            pdf.set_x(x + 6.5)
            pdf.multi_cell(0, 5.4, b.text, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
            pdf.ln(1.2)
        elif b.kind == "table":
            pdf.set_font("Body", "", 10)
            with pdf.table(line_height=6, first_row_as_headings=True) as table:
                for row in b.rows:
                    r = table.row()
                    for cell in row:
                        r.cell(cell)
            pdf.ln(3)
    pdf.output(str(path))


def render_docx(blocks: list[Block], path: Path) -> None:
    doc = Document()
    doc.core_properties.created = FIXED_DATE.replace(tzinfo=None)
    style = doc.styles["Normal"]
    style.font.name = "Arial"
    style.font.size = Pt(10.5)
    for b in blocks:
        if b.kind == "heading":
            doc.add_heading(b.text, level=b.level)
        elif b.kind in ("para", "bullet"):
            p = doc.add_paragraph(style="List Bullet" if b.kind == "bullet" else None)
            for i, part in enumerate(re.split(r"\*\*", b.text)):
                p.add_run(part).bold = i % 2 == 1
        elif b.kind == "check":
            doc.add_paragraph(("☒ " if b.checked else "☐ ") + b.text)
        elif b.kind == "table":
            table = doc.add_table(rows=len(b.rows), cols=len(b.rows[0]))
            table.style = "Table Grid"
            for r, row in enumerate(b.rows):
                for c, cell in enumerate(row):
                    table.cell(r, c).text = cell
                    if r == 0:
                        for run in table.cell(r, c).paragraphs[0].runs:
                            run.bold = True
    doc.save(str(path))


def rasterize(pdf_path: Path, dpi: int) -> list[Image.Image]:
    document = pdfium.PdfDocument(str(pdf_path))
    images = [page.render(scale=dpi / 72).to_pil() for page in document]
    document.close()
    return images


def degrade_scan(image: Image.Image, rng: random.Random) -> Image.Image:
    """An office scanner: grey, slightly skewed, noisy, a little soft, JPEG-compressed."""
    img = image.convert("L").rotate(rng.uniform(-1.4, 1.4), expand=False, fillcolor=255, resample=Image.BICUBIC)
    arr = np.asarray(img, dtype=np.float32)
    arr = arr * 0.88 + 22  # lower contrast, grey paper
    arr += np.random.default_rng(rng.randrange(1 << 30)).normal(0, 9, arr.shape)
    img = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(0.55))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=55)
    return Image.open(io.BytesIO(buf.getvalue()))


def _perspective_coefficients(src: list[tuple[float, float]], dst: list[tuple[float, float]]) -> list[float]:
    matrix = []
    for (x, y), (u, v) in zip(dst, src, strict=True):
        matrix.append([x, y, 1, 0, 0, 0, -u * x, -u * y])
        matrix.append([0, 0, 0, x, y, 1, -v * x, -v * y])
    a = np.array(matrix, dtype=np.float64)
    b = np.array(src, dtype=np.float64).reshape(8)
    return list(np.linalg.solve(a, b))


def degrade_photo(image: Image.Image, rng: random.Random) -> Image.Image:
    """A phone photo of a printed page: perspective, uneven light, warm tint, noise, rotation."""
    img = image.convert("RGB")
    w, h = img.size
    pad = int(w * 0.06)
    canvas = Image.new("RGB", (w + 2 * pad, h + 2 * pad), (92, 84, 74))  # a desk
    canvas.paste(img, (pad, pad))
    cw, ch = canvas.size
    j = lambda s: rng.uniform(0, s * cw)  # noqa: E731
    corners = [(0, 0), (cw, 0), (cw, ch), (0, ch)]
    moved = [(j(0.035), j(0.02)), (cw - j(0.03), j(0.04)), (cw - j(0.02), ch - j(0.03)), (j(0.04), ch - j(0.02))]
    canvas = canvas.transform(
        (cw, ch), Image.PERSPECTIVE, _perspective_coefficients(corners, moved), Image.BICUBIC, fillcolor=(92, 84, 74)
    )
    canvas = canvas.rotate(rng.uniform(-3, 3), resample=Image.BICUBIC, fillcolor=(92, 84, 74))
    arr = np.asarray(canvas, dtype=np.float32)
    yy, xx = np.mgrid[0:ch, 0:cw]
    light = 0.78 + 0.27 * (1 - ((xx - cw * 0.35) ** 2 + (yy - ch * 0.3) ** 2) / (cw**2 + ch**2))
    arr = arr * light[..., None] * np.array([1.0, 0.97, 0.9])
    arr += np.random.default_rng(rng.randrange(1 << 30)).normal(0, 6, arr.shape)
    img = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(0.7))
    img.thumbnail((1700, 2400))
    return img


# ─── Downloads ───────────────────────────────────────────────────────────────


def fetch(client: httpx.Client | None, url: str, cache: Path) -> bytes:
    if cache.exists():
        return cache.read_bytes()
    if client is None:
        raise SystemExit(f"--offline but {cache} is missing")
    response = client.get(url)
    response.raise_for_status()
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_bytes(response.content)
    return response.content


def _clean(text: str) -> str:
    return " ".join(text.replace("\xa0", " ").replace("\u2011", "-").split())


def legislation_blocks(tree: Any, title: str, prefixes: list[str]) -> list[Block]:
    blocks = [Block("heading", title, level=1)]
    current_section = ""
    for article in tree.iter("artikel"):
        path = article.get("bwb-ng-variabel-deel", "")
        if not any(path.startswith(p) for p in prefixes):
            continue
        for meta in list(article.iter("meta-data", "redactie")):
            meta.getparent().remove(meta)
        number = _clean(article.findtext("kop/nr") or "")
        if not number:
            continue
        section = re.search(r"/(Afdeling\d+\w*)/", path)
        if section and section.group(1) != current_section:
            current_section = section.group(1)
            afdeling = next(article.iterancestors("afdeling"), None)
            kop = afdeling.find("kop") if afdeling is not None else None
            if kop is not None:
                blocks.append(Block("heading", _clean(" ".join(kop.itertext())), level=2))
        parts: list[str] = []
        leden = article.findall("lid")
        for lid in leden or [article]:
            texts = [_clean(" ".join(el.itertext())) for el in lid if el.tag in ("al", "lijst")]
            text = " ".join(t for t in texts if t)
            if text:
                lidnr = _clean(lid.findtext("lidnr") or "")
                parts.append(f"{lidnr}. {text}" if lidnr and leden else text)
        if not parts or all(re.fullmatch(r"\[?Vervallen.*", p) for p in parts):
            continue
        blocks.append(Block("heading", f"Artikel 7:{number}", level=3))
        blocks.extend(Block("para", p) for p in parts)
    return blocks


def ruling_blocks(xml: bytes, ecli: str) -> tuple[str, list[Block], dict[str, str]]:
    root = etree.fromstring(xml)
    rdf = root.find("{http://www.w3.org/1999/02/22-rdf-syntax-ns#}RDF")
    meta: dict[str, str] = {}
    if rdf is not None:
        for el in rdf.iter():
            tag = etree.QName(el).localname if isinstance(el.tag, str) else ""
            if tag in ("creator", "date", "zaaknummer") and el.text and tag not in meta:
                meta[tag] = _clean(el.text)
    court = meta.get("creator", "")
    title = f"Uitspraak {court} {meta.get('date', '')} {ecli}".strip()
    blocks = [
        Block("heading", f"{court}", level=1),
        Block("para", f"ECLI: {ecli}"),
        Block("para", f"Uitspraakdatum (metadata Rechtspraak): {meta.get('date', '')}"),
        Block("para", f"Zaaknummer: {meta.get('zaaknummer', '')}"),
    ]
    summary = root.find(RS + "inhoudsindicatie")
    if summary is not None:
        blocks.append(Block("heading", "Inhoudsindicatie", level=2))
        blocks.append(Block("para", _clean(" ".join(summary.itertext()))))
    decision = root.find(RS + "uitspraak")
    footnotes: list[str] = []
    for child in decision if decision is not None else []:
        name = etree.QName(child).localname
        if name == "footnote":
            footnotes.append(_clean(" ".join(child.itertext())))
            continue
        heading = child.find(RS + "title")
        if heading is not None:
            blocks.append(Block("heading", _clean(" ".join(heading.itertext())), level=2))
        for part in child:
            if part is heading:
                continue
            if etree.QName(part).localname == "footnote":
                footnotes.append(_clean(" ".join(part.itertext())))
                continue
            text = _clean(" ".join(t for t in part.itertext()))
            if text:
                blocks.append(Block("para", text))
    if footnotes:
        blocks.append(Block("heading", "Voetnoten", level=2))
        blocks.extend(Block("para", f) for f in footnotes)
    return title, blocks, meta


# ─── Build ───────────────────────────────────────────────────────────────────


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def build(offline: bool) -> list[dict[str, Any]]:
    for d in (RAW, FILES, TRUTH):
        d.mkdir(parents=True, exist_ok=True)
    client = None if offline else httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=120, follow_redirects=True)
    docs: list[dict[str, Any]] = []

    # Legislation
    bwb = fetch(client, BWB_URL, RAW / f"{BWB_ID}_{BWB_VERSION}.xml")
    tree = etree.parse(io.BytesIO(bwb))
    for doc_id, title, prefixes in LEGISLATION:
        blocks = legislation_blocks(tree, title, prefixes)
        out = FILES / f"{doc_id}.pdf"
        render_pdf(blocks, out, footer=f"BW Boek 7, geldend vanaf 1 januari 2026 ({BWB_ID})")
        docs.append(
            {
                "id": doc_id,
                "file": out.relative_to(ROOT).as_posix(),
                "title": title,
                "kind": "legislation",
                "document_type": "legislation",
                "matter": None,
                "format": "pdf",
                "source": BWB_URL,
                "source_sha256": sha256(bwb),
                "articles": sum(1 for b in blocks if b.kind == "heading" and b.level == 3),
            }
        )

    # Case law
    for ecli, theme in RULINGS:
        xml = fetch(
            client,
            f"https://data.rechtspraak.nl/uitspraken/content?id={ecli}",
            RAW / f"{ecli.replace(':', '_')}.xml",
        )
        title, blocks, meta = ruling_blocks(xml, ecli)
        doc_id = "uitspraak-" + ecli.split(":", 2)[2].replace(":", "-").lower()
        out = FILES / f"{doc_id}.pdf"
        render_pdf(blocks, out, footer=ecli)
        docs.append(
            {
                "id": doc_id,
                "file": out.relative_to(ROOT).as_posix(),
                "title": title,
                "kind": "case_law",
                "document_type": "court_filing",
                "matter": None,
                "format": "pdf",
                "theme": theme,
                "ecli": ecli,
                "source": f"https://uitspraken.rechtspraak.nl/details?id={ecli}",
                "source_sha256": sha256(xml),
            }
        )

    # Fictional case files
    for source in sorted(SOURCES.glob("*.md")):
        meta, blocks = parse_markdown(source.read_text(encoding="utf-8"))
        doc_id = source.stem
        rng = random.Random(doc_id)
        fmt = meta["formats"]
        truth = TRUTH / f"{doc_id}.txt"
        truth.write_text(plain_text(blocks), encoding="utf-8")
        tables = [b.rows for b in blocks if b.kind == "table"]
        checks = [{"label": b.text, "checked": b.checked} for b in blocks if b.kind == "check"]
        (TRUTH / f"{doc_id}.json").write_text(
            json.dumps({"tables": tables, "selection_marks": checks}, ensure_ascii=False, indent=1), encoding="utf-8"
        )

        clean_pdf = FILES / f"{doc_id}.clean.pdf"
        render_pdf(blocks, clean_pdf)
        if fmt == "pdf":
            out = FILES / f"{doc_id}.pdf"
            clean_pdf.replace(out)
        elif fmt == "docx":
            out = FILES / f"{doc_id}.docx"
            render_docx(blocks, out)
            clean_pdf.unlink()
        elif fmt == "scan":
            pages = [degrade_scan(p, rng) for p in rasterize(clean_pdf, 200)]
            out = FILES / f"{doc_id}.pdf"
            pages[0].save(out, "PDF", resolution=200, save_all=True, append_images=pages[1:])
        elif fmt == "photo":
            pages = rasterize(clean_pdf, 170)
            if len(pages) != 1:
                raise SystemExit(f"{doc_id}: a photo source must fit on one page")
            out = FILES / f"{doc_id}.jpg"
            degrade_photo(pages[0], rng).save(out, "JPEG", quality=78)
        else:
            raise SystemExit(f"{doc_id}: unknown format {fmt}")

        docs.append(
            {
                "id": doc_id,
                "file": out.relative_to(ROOT).as_posix(),
                "title": meta["title"],
                "kind": "case_file",
                "document_type": meta["document_type"],
                "matter": meta["matter"],
                "format": fmt,
                "source": source.relative_to(ROOT).as_posix(),
                "clean_pdf": clean_pdf.relative_to(ROOT).as_posix() if clean_pdf.exists() else None,
                "ground_truth": truth.relative_to(ROOT).as_posix(),
            }
        )

    # The original sample files
    for doc_id, rel, document_type, matter in SAMPLES:
        path = ROOT / rel
        docs.append(
            {
                "id": doc_id,
                "file": rel,
                "title": path.name,
                "kind": "case_file",
                "document_type": document_type,
                "matter": matter,
                "format": "txt",
                "source": rel,
            }
        )

    for d in docs:
        d["sha256"] = sha256((ROOT / d["file"]).read_bytes())
    return docs


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--offline", action="store_true", help="use cached downloads only")
    args = parser.parse_args()
    try:
        import truststore

        truststore.inject_into_ssl()
    except ImportError:
        pass
    docs = build(args.offline)
    MANIFEST.write_text(
        json.dumps({"bwb_version": BWB_VERSION, "documents": docs}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    by_format: dict[str, int] = {}
    for d in docs:
        by_format[d["format"]] = by_format.get(d["format"], 0) + 1
    print(f"{len(docs)} documents -> {MANIFEST.relative_to(ROOT)}: {by_format}")


if __name__ == "__main__":
    main()
