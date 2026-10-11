"""Read a case folder: every document as text for Claude, PDFs and images also as native attachments,
and any machine-readable service contract (OpenAPI JSON/YAML) that is already there."""
import base64
import csv
import io
import json
import pathlib

TEXT = {".md", ".txt", ".csv", ".tsv", ".json", ".yaml", ".yml", ".eml", ".html", ".xml", ".log"}
IMAGES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif", ".webp": "image/webp"}


def read_docx(p):
    import docx
    d = docx.Document(str(p))
    out = [para.text for para in d.paragraphs if para.text.strip()]
    for ti, t in enumerate(d.tables):
        out.append(f"[table {ti + 1}]")
        for row in t.rows:
            out.append(" | ".join(c.text.strip() for c in row.cells))
    return "\n".join(out)


def read_xlsx(p):
    import openpyxl
    wb = openpyxl.load_workbook(str(p), data_only=True)
    out = []
    for ws in wb.worksheets:
        out.append(f"[sheet {ws.title}]")
        for row in ws.iter_rows(values_only=True):
            if any(c is not None and str(c).strip() for c in row):
                out.append("\t".join("" if c is None else str(c) for c in row))
    return "\n".join(out)


def read_pdf(p):
    import pypdf
    r = pypdf.PdfReader(str(p))
    return "\n".join((pg.extract_text() or "") for pg in r.pages)


def is_openapi(obj):
    return isinstance(obj, dict) and ("openapi" in obj or "swagger" in obj) and isinstance(obj.get("paths"), dict)


def load_case(folder, log=print):
    """Returns (text_blocks, attachments, openapi_or_None, test_customers_files).
    text_blocks: list of (relative name, text). attachments: Claude content blocks for PDFs/images."""
    folder = pathlib.Path(folder)
    files = sorted(p for p in folder.rglob("*") if p.is_file() and not p.name.startswith(".") and "__MACOSX" not in p.parts)
    # README and BRIEF first, as the case instructions say
    files.sort(key=lambda p: (0 if p.name.upper().startswith("BRIEF") else 1 if p.name.upper().startswith("README") else 2, str(p)))
    texts, attach, openapi = [], [], None
    for p in files:
        rel = str(p.relative_to(folder))
        ext = p.suffix.lower()
        try:
            if ext in TEXT:
                t = p.read_text(errors="replace")
                if ext in (".json", ".yaml", ".yml"):
                    try:
                        obj = json.loads(t) if ext == ".json" else __import__("yaml").safe_load(t)
                        if is_openapi(obj) and openapi is None:
                            openapi = obj
                            log(f"  service contract (OpenAPI) found: {rel}")
                    except Exception:
                        pass
                texts.append((rel, t))
            elif ext == ".docx":
                texts.append((rel, read_docx(p)))
            elif ext in (".xlsx", ".xlsm"):
                texts.append((rel, read_xlsx(p)))
            elif ext == ".pdf":
                texts.append((rel, read_pdf(p)))
                if p.stat().st_size < 20_000_000:
                    attach.append({"type": "document", "title": rel,
                                   "source": {"type": "base64", "media_type": "application/pdf", "data": base64.b64encode(p.read_bytes()).decode()}})
            elif ext in IMAGES:
                if p.stat().st_size < 5_000_000:
                    attach.append({"type": "image", "source": {"type": "base64", "media_type": IMAGES[ext], "data": base64.b64encode(p.read_bytes()).decode()}})
                    texts.append((rel, "(image, attached)"))
            else:
                texts.append((rel, f"(file of type {ext}, not read)"))
            log(f"  read {rel} ({ext}, {len(texts[-1][1])} chars)")
        except Exception as e:
            texts.append((rel, f"(could not read: {e})"))
            log(f"  could not read {rel}: {e}")
    return texts, attach, openapi


MAX_DOC_CHARS = 60_000   # big data dumps (e.g. full test fixtures) are sampled, documents are kept whole


def as_prompt(texts):
    out = []
    for name, t in texts:
        if len(t) > MAX_DOC_CHARS:
            t = (t[:MAX_DOC_CHARS // 2] + f"\n\n... [{len(t) - MAX_DOC_CHARS // 2} more characters of this large data file left out; "
                 "the beginning above shows its structure] ...")
        out.append(f"=== DOCUMENT: {name} ===\n{t}")
    return "\n\n".join(out)
