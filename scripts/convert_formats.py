#!/usr/bin/env python3
"""Per-format -> Markdown converters for the corpus conversion pipeline.

Each handler takes a source path and returns (markdown_text, flags) where
flags is a list of {"type": ..., "detail": ...} dicts describing follow-up
work (vision layer, numbering review, attachments to route). Handlers never
write files; the driver (convert.py) owns all output.

Engine choices (measured on a ~400-document professional corpus, 2026-07/08):
- PDF / DOCX: docling, placeholder image mode (markitdown corrupts two-column
  PDFs and leaks Word hyperlink targets).
- PPTX: python-pptx, NOT docling — docling flattens decks into one stream
  with no slide boundaries (verified on a 16-slide deck: 0 headings), which
  destroys slide-number citation anchors. python-pptx keeps per-slide
  structure and is the only route to speaker notes.
- HTML: plain html->md library; already markup, docling adds nothing.
- EML/MSG: header-aware parsing, not layout conversion.
- XLSX and friends are policy-routed (catalogue, don't convert) by the driver.
"""
import io
import os
import re
import threading

MIN_CHARS = 200        # below this a whole-document conversion is "low text"
LOWTEXT_SLIDE = 200    # per-slide threshold for the PPTX vision-layer flag


def wp(path):
    """Windows long-path safe form (bypasses the legacy 260-char MAX_PATH)."""
    p = os.path.abspath(path)
    if os.name == "nt" and not p.startswith("\\\\?\\"):
        return "\\\\?\\" + p
    return p


def _visible_len(text):
    return len("".join(ch for ch in text if not ch.isspace()))


# --------------------------------------------------------------------------
# PDF / DOCX via docling
# --------------------------------------------------------------------------
_tls = threading.local()


def _docling_converter():
    # One converter per thread: DocumentConverter is not documented as
    # thread-safe, and construction is cheap next to conversion.
    #
    # OCR is OFF by default (CONVERT_OCR=1 re-enables): the OCR preprocessor
    # rasterizes every page at high resolution, and image-heavy PDFs (maps,
    # dashboards) exhausted memory with std::bad_alloc, killing whole batch
    # runs (observed twice, 2026-08-07). Born-digital PDFs don't need OCR;
    # genuinely scanned ones come out under MIN_CHARS, get the `low-text`
    # flag, and go through the targeted OCR/vision finishing layer one file
    # at a time.
    conv = getattr(_tls, "converter", None)
    if conv is None:
        # Recent docling/torch tries to JIT-compile the layout model via
        # torch inductor, which needs a C++ compiler (MSVC `cl` on Windows)
        # and crashes the whole conversion when none exists. Eager mode is
        # fine for inference — force it unless the user overrode it.
        os.environ.setdefault("TORCHDYNAMO_DISABLE", "1")
        from docling.document_converter import DocumentConverter, PdfFormatOption
        from docling.datamodel.base_models import InputFormat
        from docling.datamodel.pipeline_options import PdfPipelineOptions
        if os.environ.get("CONVERT_OCR"):
            conv = DocumentConverter()
        else:
            opts = PdfPipelineOptions(do_ocr=False)
            conv = DocumentConverter(format_options={
                InputFormat.PDF: PdfFormatOption(pipeline_options=opts)})
        _tls.converter = conv
    return conv


def _docling_convert(src):
    from docling_core.types.doc import ImageRefMode
    res = _docling_converter().convert(src)
    return res.document.export_to_markdown(image_mode=ImageRefMode.PLACEHOLDER)


def convert_pdf(src):
    text = _docling_convert(src)
    flags = []
    if _visible_len(text) < MIN_CHARS:
        flags.append({
            "type": "low-text",
            "detail": f"{_visible_len(text)} visible chars — likely a scanned/"
                      "graphic PDF; queue for the OCR/vision finishing layer",
        })
    return text, flags


_NUM_SUB = re.compile(r"^(\d+(?:\.\d+)+)[.)]?\s")
_NUM_TOP = re.compile(r"^(\d+)[.)]\s")


def _numbering_check(text):
    """Heuristic for docling's fabricated clause numbers on auto-numbered
    Word documents: flag sub-numbers whose top-level component disagrees with
    the most recent top-level section seen. A flag means "review before
    citing clause numbers", not "definitely wrong".
    """
    top = None
    mismatch = total = 0
    for line in text.splitlines():
        line = line.strip()
        if line[:2] in ("- ", "* "):
            line = line[2:]
        m = _NUM_SUB.match(line)
        if m:
            total += 1
            if top is not None and int(m.group(1).split(".")[0]) != top:
                mismatch += 1
            continue
        m = _NUM_TOP.match(line)
        if m:
            top = int(m.group(1))
    if mismatch:
        return [{
            "type": "numbering",
            "detail": f"{mismatch}/{total} hierarchical numbers disagree with "
                      "their enclosing top-level section — docling may have "
                      "fabricated clause numbers; finishing layer before citing",
        }]
    return []


def convert_docx(src):
    text = _docling_convert(src)
    flags = []
    if _visible_len(text) < MIN_CHARS:
        flags.append({
            "type": "low-text",
            "detail": f"{_visible_len(text)} visible chars — scan-in-a-wrapper; "
                      "queue for the OCR/vision finishing layer",
        })
    flags.extend(_numbering_check(text))
    return text, flags


# --------------------------------------------------------------------------
# PPTX via python-pptx
# --------------------------------------------------------------------------
def _pptx_shape_md(shape, lines):
    from pptx.enum.shapes import MSO_SHAPE_TYPE
    try:
        stype = shape.shape_type
    except Exception:
        stype = None
    if stype == MSO_SHAPE_TYPE.GROUP:
        for sub in shape.shapes:
            _pptx_shape_md(sub, lines)
        return
    if getattr(shape, "has_table", False):
        rows = [[c.text.strip().replace("\n", " ").replace("|", "\\|")
                 for c in r.cells] for r in shape.table.rows]
        if rows:
            lines.append("| " + " | ".join(rows[0]) + " |")
            lines.append("|" + "---|" * len(rows[0]))
            for r in rows[1:]:
                lines.append("| " + " | ".join(r) + " |")
            lines.append("")
        return
    if getattr(shape, "has_chart", False):
        lines.append("<!-- chart -->")
        lines.append("")
        return
    if stype == MSO_SHAPE_TYPE.PICTURE:
        lines.append("<!-- image -->")
        lines.append("")
        return
    if getattr(shape, "has_text_frame", False):
        text = shape.text_frame.text.strip()
        if text:
            lines.append(text)
            lines.append("")


def convert_pptx(src):
    from pptx import Presentation
    with open(wp(src), "rb") as fh:
        prs = Presentation(fh)

    out, low = [], []
    for i, slide in enumerate(prs.slides, 1):
        title = ""
        try:
            if slide.shapes.title is not None:
                title = slide.shapes.title.text.strip()
        except Exception:
            pass
        out.append(f"## Slide {i}" + (f" — {title}" if title else ""))
        out.append("")

        lines = []
        for shape in slide.shapes:
            if title and shape is slide.shapes.title:
                continue
            _pptx_shape_md(shape, lines)
        out.extend(lines)

        # Visual density is judged on the slide face only, notes excluded.
        body = "".join(l for l in lines if not l.startswith("<!--"))
        if _visible_len(body) + _visible_len(title) < LOWTEXT_SLIDE:
            low.append(i)

        if slide.has_notes_slide:
            note = slide.notes_slide.notes_text_frame.text.strip()
            if note:
                out.append("**Speaker notes:**")
                out.append("")
                out.append("> " + note.replace("\n", "\n> "))
                out.append("")

    flags = []
    if low:
        flags.append({
            "type": "pptx-low-text-slides",
            "detail": f"{len(low)}/{len(prs.slides)} slides under "
                      f"{LOWTEXT_SLIDE} chars: {low} — visual content; queue "
                      "for the per-slide vision finishing layer",
        })
    return "\n".join(out), flags


# --------------------------------------------------------------------------
# HTML — already markup; no docling
# --------------------------------------------------------------------------
def _html_to_md(html):
    try:
        from markdownify import markdownify
        return markdownify(html, heading_style="ATX")
    except ImportError:
        pass
    try:
        import html2text
        h = html2text.HTML2Text()
        h.body_width = 0
        return h.handle(html)
    except ImportError:
        raise RuntimeError(
            "no html->md library in this interpreter: pip install markdownify "
            "(preferred) or html2text, or convert with pandoc")


def convert_html(src):
    with io.open(wp(src), encoding="utf-8", errors="replace") as fh:
        raw = fh.read()
    # Faithful conversion only. Boilerplate-stripping for saved web articles
    # is a per-file judgement call — route those through the defuddle skill.
    return _html_to_md(raw), []


# --------------------------------------------------------------------------
# EML / MSG — parse, don't layout-convert
# --------------------------------------------------------------------------
def _email_frontmatter(fields):
    lines = ["---"]
    for key, val in fields:
        if val:
            val = str(val).replace('"', "'").replace("\n", " ").strip()
            lines.append(f'{key}: "{val}"')
    lines.append("---")
    return "\n".join(lines)


def convert_eml(src):
    import email
    from email import policy
    with open(wp(src), "rb") as fh:
        msg = email.message_from_binary_file(fh, policy=policy.default)

    fm = _email_frontmatter([
        ("from", msg.get("From")), ("to", msg.get("To")),
        ("cc", msg.get("Cc")), ("date", msg.get("Date")),
        ("subject", msg.get("Subject")),
    ])

    body_part = msg.get_body(preferencelist=("plain", "html"))
    body = ""
    if body_part is not None:
        content = body_part.get_content()
        # Emails with a UTF-8 body but no declared charset decode as ASCII
        # with replacement characters; if the raw payload is clean UTF-8,
        # prefer that over the mangled default decode.
        if "�" in content:
            try:
                content = body_part.get_payload(decode=True).decode("utf-8")
            except (UnicodeDecodeError, AttributeError):
                pass
        body = _html_to_md(content) if body_part.get_content_subtype() == "html" else content

    attachments = [p.get_filename() for p in msg.iter_attachments()
                   if p.get_filename()]
    flags = []
    text = fm + "\n\n" + body.strip() + "\n"
    if attachments:
        text += "\n**Attachments (not inlined):** " + ", ".join(attachments) + "\n"
        flags.append({
            "type": "attachments",
            "detail": "route each through its own format pipeline: "
                      + ", ".join(attachments),
        })
    return text, flags


def convert_msg(src):
    try:
        import extract_msg
    except ImportError:
        raise RuntimeError("extract-msg not installed: pip install extract-msg")
    m = extract_msg.Message(wp(src))
    fm = _email_frontmatter([
        ("from", m.sender), ("to", m.to), ("cc", m.cc),
        ("date", m.date), ("subject", m.subject),
    ])
    attachments = [a.longFilename or a.shortFilename for a in m.attachments]
    flags = []
    text = fm + "\n\n" + (m.body or "").strip() + "\n"
    if attachments:
        text += "\n**Attachments (not inlined):** " + ", ".join(attachments) + "\n"
        flags.append({
            "type": "attachments",
            "detail": "route each through its own format pipeline: "
                      + ", ".join(attachments),
        })
    return text, flags


HANDLERS = {
    ".pdf": convert_pdf,
    ".docx": convert_docx,
    ".doc": convert_docx,
    ".pptx": convert_pptx,
    ".html": convert_html,
    ".htm": convert_html,
    ".eml": convert_eml,
    ".msg": convert_msg,
}

# Copied into the output tree untouched.
PASSTHROUGH = {".md", ".txt"}

# Deliberately NOT converted — the document-vs-dataset policy: form-template
# workbooks (XLSForms) can be rendered to markdown, data workbooks should be
# catalogued (see the form-catalogue skill) and left in their native format,
# and CSV choice lists left untouched. A spreadsheet flattened to markdown
# is neither a readable document nor a queryable dataset.
POLICY_SKIP = {".xlsx", ".xls", ".xlsm", ".csv"}
