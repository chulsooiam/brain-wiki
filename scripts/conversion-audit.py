#!/usr/bin/env python3
"""conversion-audit.py — corpus-wide conversion fidelity audit.

Docling can silently drop whole pages and truncate tables at page breaks
(exit 0, no warning). The per-file QA gate (bin/review-conversion.sh)
catches this for documents it reviews; this script is the CORPUS-WIDE
sweep: for every PDF original, extract ground truth with pdftotext
(poppler) and compute vocabulary recall against the converted markdown —
the share of distinct ground-truth words (4+ letters) present in the
conversion. Silent page drops pull recall down even when the converted
file "looks" complete.

Usage:
    conversion-audit.py <originals-root> [--threshold 0.97] [--json OUT]

<originals-root> is the tree of original PDFs whose conversions live under
the vault's .sources/ at the same relative paths (plus ".md"). Scanned
originals (pdftotext yields <50 words) are skipped — recall is undefined
for them; convert those with OCR and review manually.

Exit codes: 0 = all audited files at/above threshold; 1 = at least one
flagged; 2 = usage/tooling error.

Interpreting results: low recall on heavily DESIGNED documents (posters,
factsheets, slides-as-pdf) may be a docling capability limit rather than a
regression — fresh reconversion will not help; use the LLM finishing layer
(read the PDF directly) per CLAUDE.md. For ordinary text documents, low
recall usually means dropped pages: reconvert and re-run the QA gate.
"""
import argparse
import glob
import io
import json
import os
import re
import shutil
import subprocess
import sys

VAULT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORD = re.compile(r"[a-z]{4,}")


def find_pdftotext():
    exe = shutil.which("pdftotext")
    if exe:
        return exe
    hits = glob.glob(os.path.join(
        os.environ.get("LOCALAPPDATA", ""),
        "Microsoft", "WinGet", "Packages", "*Poppler*", "poppler-*",
        "Library", "bin", "pdftotext.exe"))
    if hits:
        return hits[0]
    print("ERR: pdftotext not found — install poppler (poppler-utils / "
          "winget oschwartz10612.Poppler)", file=sys.stderr)
    sys.exit(2)


def words(text):
    return set(WORD.findall(text.lower()))


def main():
    ap = argparse.ArgumentParser(
        description="Vocabulary-recall audit of PDF conversions.")
    ap.add_argument("originals_root",
                    help="tree of original PDFs mirrored under .sources/")
    ap.add_argument("--threshold", type=float, default=0.97)
    ap.add_argument("--json", dest="json_out",
                    help="write flagged list as JSON")
    args = ap.parse_args()

    if not os.path.isdir(args.originals_root):
        print(f"ERR: no such directory: {args.originals_root}",
              file=sys.stderr)
        return 2
    pdftotext = find_pdftotext()

    results, skipped = [], []
    for pdf in glob.glob(os.path.join(args.originals_root, "**", "*.pdf"),
                         recursive=True):
        rel = os.path.relpath(pdf, args.originals_root).replace("\\", "/")
        conv = os.path.join(VAULT_ROOT, ".sources", rel + ".md")
        if not os.path.exists(conv):
            skipped.append((rel, "no conversion on disk"))
            continue
        try:
            out = subprocess.run([pdftotext, pdf, "-"], capture_output=True,
                                 timeout=300)
        except subprocess.TimeoutExpired:
            skipped.append((rel, "pdftotext timeout"))
            continue
        gt = words(out.stdout.decode("utf-8", errors="replace"))
        if len(gt) < 50:
            skipped.append((rel, f"scanned/low-text original ({len(gt)} words)"))
            continue
        md = words(io.open(conv, encoding="utf-8", errors="replace").read())
        results.append((len(gt & md) / len(gt), rel, len(gt)))

    results.sort()
    flagged = [(r, rel) for r, rel, _ in results if r < args.threshold]

    print(f"audited {len(results)} PDFs | skipped {len(skipped)} | "
          f"flagged {len(flagged)} (< {args.threshold:.0%})")
    for r, rel, n in results:
        mark = "  <-- FLAG" if r < args.threshold else ""
        print(f"  {r:6.1%}  ({n:5d} words)  {rel}{mark}")
    if skipped:
        print("skipped:")
        for rel, why in skipped:
            print(f"  {rel}: {why}")

    if args.json_out:
        json.dump([{"recall": r, "file": rel} for r, rel in flagged],
                  open(args.json_out, "w"), indent=1)
    return 1 if flagged else 0


if __name__ == "__main__":
    sys.exit(main())
