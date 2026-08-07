---
name: corpus-convert
description: "Convert source documents (PDF, DOCX, PPTX, HTML, EML/MSG) into the Markdown corpus under .sources/ that corpus-query indexes. Per-format engines, resumable batch mode, and QA flags that drive the finishing layers. Triggers on: convert this document, convert the corpus, batch convert, add documents to the corpus, ingest raw documents, pdf to markdown, docx to markdown, pptx to markdown, convert sources."
allowed-tools: Read Bash
---

# corpus-convert: Source Documents → Markdown Corpus

The conversion feature that feeds the corpus tier. One driver
(`scripts/convert.py`) with per-format handlers (`scripts/convert_formats.py`);
output lands in `.sources/`, which `corpus-retrieve.py` auto-indexes on first
query (default-on hybrid retrieval — no manual index step).

**Origin**: part of the professional-corpus extension; not in upstream
v1.9.x.

---

## Run

```bash
# single file → markdown on stdout, QA flags on stderr
PYTHONUTF8=1 python3 scripts/convert.py "path/to/doc.pptx"

# batch: mirror SRC's tree into .sources/ (resumable — re-run to resume)
PYTHONUTF8=1 python3 scripts/convert.py --batch "path/to/source-tree" --workers 4
```

Every converted file becomes `<name>.<ext>.md` (extension kept in the name so
provenance survives). Batch bookkeeping lands in the destination:
`.convert_done.txt` (resume state), `.convert_flags.jsonl` (finishing-layer
worklist), `.convert_errors.txt`, `.convert_skipped.txt`, `.convert.log`.

Organize SRC's top-level folders as curation tiers ("Tier 1", "Tier 2",
"Reference Material", …) — the corpus indexer stamps each chunk's `tier`
from the top-level folder name (see the `corpus-query` skill).

## Per-format decisions

| ext | engine | why / flags raised |
|---|---|---|
| pdf | docling, placeholder images, **OCR off** (`CONVERT_OCR=1` re-enables) | markitdown corrupts 2-column layout. OCR default-off: the OCR rasterizer exhausted memory on image-heavy PDFs and killed whole batch runs; born-digital PDFs don't need it. `low-text` flag (<200 chars) = scanned → OCR/vision finishing layer |
| docx, doc | docling, placeholder images | + `numbering` flag: docling fabricates clause numbers on auto-numbered Word docs (heuristic check built in); never cite clause numbers from a flagged file. Verify with `scripts/check-numbering.py`, repair with `scripts/fix-numbering.py` |
| pptx | **python-pptx, not docling** | docling flattens decks to one stream with no slide boundaries; python-pptx keeps `## Slide N — title` anchors (slide numbers are REAL citation anchors, unlike docx clause numbers), tables, `<!-- image -->`/`<!-- chart -->` placeholders, speaker notes as blockquotes. `pptx-low-text-slides` flag (<200 chars/slide) = visual slide → per-slide vision finishing layer |
| html, htm | markdownify → html2text fallback | already markup; faithful conversion only — boilerplate-strip saved web articles via the defuddle skill instead, per-file judgement |
| eml | stdlib `email` | headers → YAML frontmatter, text/plain body preferred, quoted chains kept; `attachments` flag — route each attachment through its own format pipeline. **Emails carry third-party personal content: make an explicit privacy call before indexing them at all** |
| msg | extract-msg | same shape as eml; `pip install extract-msg` when needed |
| md, txt | copied through | |
| xlsx, xls, xlsm, csv | **policy skip** | document-vs-dataset policy: XLSForm templates can be rendered, data workbooks are catalogued (form-catalogue skill) and left in Excel, choice lists untouched |

## Known limitations

- **Text-dense diagrams evade the low-text heuristic** — a flowchart's
  fragments can add up past the threshold while the arrows (the actual
  meaning) are lost. Only the visual QA gate catches this.
- Legacy binary `.doc` sometimes fails outright in docling; convert those
  files manually or accept the error-log entry.
- `<!-- image -->` placeholders mean the content exists only in the
  original — sometimes entire data tables.

## QA gates and finishing layers

After conversion, before trusting the output for citation:

1. **Visual compare** (`bin/review-conversion.sh "<converted.md>" ["<original>"]`) —
   render the original (PDF directly; DOCX via `scripts/docx-to-pdf.ps1`,
   Word COM; PPTX via PowerPoint COM), rasterize with pdftoppm, and LLM-compare
   against the markdown.
2. **Numbering audit** for `numbering`-flagged DOCX: `scripts/check-numbering.py`
   verifies, `scripts/fix-numbering.py` repairs (regex alone cannot — LLM
   review is part of the loop).
3. **Vision layer** for `low-text` PDFs and `pptx-low-text-slides`: render the
   flagged pages/slides and describe them; append as
   `**Slide description (vision):**` blocks.

`.convert_flags.jsonl` is the authoritative worklist for all three.

## Related

- `corpus-query` skill — retrieval over the converted corpus (auto-indexes)
- `combined-query` skill — both tiers at once
- `form-catalogue` skill — the Excel side of the document-vs-dataset policy
- `docs/corpus-query.md` — architecture of the two-tier retrieval layer
