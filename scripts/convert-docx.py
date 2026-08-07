#!/usr/bin/env python3
"""Convert a DOCX/DOC to Markdown with docling (vault standard, 2026-07-27).

Replaces markitdown for Word sources. Docling wins on the three things that
matter downstream: it does not emit whole sections as one 25k-char single-cell
table row (which the ~2000-char retrieval chunker would slice into broken table
fragments), it drops Word's local hyperlink targets (which leak other people's
usernames), and it is the same engine already used for PDFs.

Images are always PLACEHOLDER. The docling default embeds base64 blobs.

Usage:
    convert-docx.py <source.docx> [-o <out.md>] [--stdout] [--force]

Exit codes:
  0 — success
  1 — conversion failed
  2 — usage error
  3 — output exists and --force not given
"""
import argparse
import sys
import time
from pathlib import Path

EXIT_FAIL = 1
EXIT_USAGE = 2
EXIT_EXISTS = 3


def log(msg):
    print(msg, file=sys.stderr, flush=True)


def convert(src: Path) -> str:
    # Imported lazily so --help works without the heavy docling import chain.
    from docling.document_converter import DocumentConverter
    from docling_core.types.doc import ImageRefMode

    converter = DocumentConverter()
    result = converter.convert(src)
    return result.document.export_to_markdown(image_mode=ImageRefMode.PLACEHOLDER)


def main():
    ap = argparse.ArgumentParser(description="DOCX -> Markdown via docling.")
    ap.add_argument("source", help="Path to the .docx/.doc file")
    ap.add_argument("-o", "--out", help="Output .md path. Default: alongside "
                                        "the source as <name>.<ext>.md")
    ap.add_argument("--stdout", action="store_true", help="Write to stdout instead")
    ap.add_argument("--force", action="store_true", help="Overwrite existing output")
    args = ap.parse_args()

    src = Path(args.source)
    if not src.is_file():
        log(f"ERR: not a file: {src}")
        return EXIT_USAGE
    if src.suffix.lower() not in (".docx", ".doc"):
        log(f"ERR: expected .docx/.doc, got {src.suffix!r}")
        return EXIT_USAGE

    # Keep the original extension in the name (report.docx.md) — vault
    # convention, so the source format stays visible after conversion.
    out = Path(args.out) if args.out else src.with_name(src.name + ".md")
    if not args.stdout and out.exists() and not args.force:
        log(f"ERR: {out} exists (use --force)")
        return EXIT_EXISTS

    t0 = time.time()
    try:
        md = convert(src)
    except Exception as e:
        log(f"ERR: docling failed on {src.name}: {type(e).__name__}: {e}")
        if "python-docx" in str(e):
            log("HINT: check for a stray docx.py on sys.path shadowing the "
                "real package before reinstalling.")
        return EXIT_FAIL

    if args.stdout:
        sys.stdout.write(md)
    else:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(md, encoding="utf-8")

    lines = md.splitlines()
    log(f"OK {len(md)} chars, {len(lines)} lines, "
        f"max line {max((len(l) for l in lines), default=0)}, "
        f"{time.time() - t0:.1f}s"
        + ("" if args.stdout else f" -> {out}"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
