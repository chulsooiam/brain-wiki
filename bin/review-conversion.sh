#!/usr/bin/env bash
# review-conversion.sh — QA gate for document→Markdown conversions (vault-local, 2026-07-27).
#
# Two modes:
#   1. Fidelity compare (preferred): pass the converted md AND the original
#      document. The claude CLI reads BOTH (PDFs are read as rendered pages)
#      and verifies completeness + accuracy + readability.
#   2. Standalone review: pass only the md. Checks readability/structure only
#      (cannot detect silently missing or altered content).
#
# Usage:
#   bash bin/review-conversion.sh "<converted.md>" ["<original.pdf|docx|...>"]
#
# Exit codes: 0 = PASS, 1 = FAIL (issues listed on stdout), 2 = usage/error.

set -euo pipefail

f_md="${1:-}"
f_orig="${2:-}"
[ -f "$f_md" ] || { echo "usage: review-conversion.sh <converted.md> [<original>]" >&2; exit 2; }
command -v claude >/dev/null 2>&1 || { echo "ERR: claude CLI not on PATH" >&2; exit 2; }

VERDICT_FMT='Reply with EXACTLY this format:
VERDICT: PASS  (or)  VERDICT: FAIL
ISSUES:
- <one line per real issue, empty if none; max 8>
RECOMMENDATION: <one line: "ingest as-is" | "polish with LLM finishing layer" | "re-convert with different tool" | "needs OCR/LLM read - no text layer">'

if [ -n "$f_orig" ]; then
  [ -f "$f_orig" ] || { echo "ERR: original not found: $f_orig" >&2; exit 2; }
  # absolute Windows-style paths for the Read tool
  abs_md=$(cygpath -m "$(realpath "$f_md")" 2>/dev/null || realpath "$f_md")
  abs_orig=$(cygpath -m "$(realpath "$f_orig")" 2>/dev/null || realpath "$f_orig")

  prompt="You are a strict QA reviewer for a document-to-Markdown conversion.
ORIGINAL document: $abs_orig
CONVERTED Markdown: $abs_md

Use the Read tool on BOTH files. For PDFs read the pages visually (use the pages parameter; for documents over 20 pages, read the first 10, a middle range, and the last 5 — judge sampled fidelity).

Verify, in priority order:
(1) COMPLETENESS: every section/heading of the original appears in the md; nothing silently dropped or truncated.
(2) ACCURACY: spot-check numbers, table cells, names and dates against the original — flag any value that differs; flag column-interleaving (sentences merged across layout columns).
(3) HUMAN readability of the md: flowing paragraphs, no mid-sentence hard breaks, no split-word or spacing artifacts.
(4) MACHINE readability of the md: proper heading hierarchy, real list syntax, valid Markdown tables, no base64 blobs, no mojibake.
Ignore: repeated page headers/footers, dotted TOC leaders, purely decorative imagery (note only if data-carrying charts were lost).
$VERDICT_FMT"

  out=$(claude -p "$prompt" --allowedTools "Read" 2>/dev/null) \
    || { echo "ERR: claude CLI call failed" >&2; exit 2; }
else
  size=$(wc -c < "$f_md")
  if [ "$size" -gt 81920 ]; then
    sample="$(head -c 61440 "$f_md")
...
[FILE TRUNCATED FOR REVIEW — ${size} bytes total; tail follows]
...
$(tail -c 20480 "$f_md")"
  else
    sample="$(cat "$f_md")"
  fi

  prompt="You are a strict QA reviewer for a document-to-Markdown conversion (docling or markitdown output). The original is NOT available; judge the Markdown alone on BOTH:
(1) HUMAN readability: paragraphs flow naturally with NO hard line breaks mid-sentence; no stray spacing or split-hyphenation artifacts; text reads as if from the original document.
(2) MACHINE readability: valid Markdown structure — heading hierarchy present and sensible, lists are real list syntax, tables are valid Markdown tables, no base64 image blobs, no garbled/mojibake text, document is not empty or truncated mid-content.
Ignore: dotted table-of-contents artifacts, page header/footer repetition, minor double spaces inside otherwise-flowing paragraphs.
$VERDICT_FMT"

  out=$(printf '%s' "$sample" | claude -p "$prompt" 2>/dev/null) \
    || { echo "ERR: claude CLI call failed" >&2; exit 2; }
fi

printf '%s\n' "$out"

case "$out" in
  *"VERDICT: PASS"*) exit 0 ;;
  *)                 exit 1 ;;
esac
