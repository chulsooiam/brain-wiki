#!/usr/bin/env python3
"""Detect fabricated clause numbers in docling DOCX output (2026-07-27).

Docling reconstructs hierarchical numbering for Word auto-numbered lists and
gets it wrong when numbering restarts: on a UNESCO policy document, 18 of 42
sub-items (43%) were numbered against the wrong parent — `1.1.`, `1.2.1.`
nested under section `2. Public Access`.

For a policy or SOP that is a correctness bug, not a formatting nit: anyone
citing clause 1.2.4 from the output would cite the wrong clause. Run this on
every numbered document before ingesting.

Only reports MISMATCHES against the enclosing top-level section. It does not
try to validate sibling ordering — docling gets that right, and flagging it
would bury the real signal.

Usage:
    check-numbering.py <converted.md> [--quiet]

Exit codes:
  0 — no mismatches (or no numbering found)
  1 — mismatches found
  2 — usage error
"""
import argparse
import io
import re
import sys

EXIT_BAD = 1
EXIT_USAGE = 2

# "2. **Public Access**" or "2. Public Access" at column 0
SECTION_RE = re.compile(r"^(\d+)\.\s+\*{0,2}\S")
# "    - 1.2.1. The public may consult ..." (docling nests as bullets).
# The leading indent is OPTIONAL: docling sometimes drops a sub-item to column
# 0 while keeping its composite number (UNESCO Part V items 5.4/5.5 landed
# there). Requiring indentation hid that class entirely — 15 files carry it.
SUBITEM_RE = re.compile(r"^\s*[-*]\s+(\d+)\.(\d+)\.")


def main():
    ap = argparse.ArgumentParser(description="Check docling clause numbering.")
    ap.add_argument("path")
    ap.add_argument("--quiet", action="store_true",
                    help="Print only the summary line")
    args = ap.parse_args()

    try:
        text = io.open(args.path, encoding="utf-8", errors="ignore").read()
    except OSError as e:
        print(f"ERR: {e}", file=sys.stderr)
        return EXIT_USAGE

    section = None
    good = 0
    bad = []
    for n, line in enumerate(text.splitlines(), 1):
        m = SECTION_RE.match(line)
        if m:
            section = m.group(1)
            continue
        m2 = SUBITEM_RE.match(line)
        if m2 and section:
            if m2.group(1) == section:
                good += 1
            else:
                bad.append((n, section, line.strip()[:100]))

    # Second defect class, found 2026-07-27 while QA-reviewing the repaired
    # files: docling also breaks the TOP-LEVEL counter, emitting Parts III/IV/V
    # of the UNESCO policy as `1.` each. Sub-items then agree with their broken
    # parent, so the check above reports a clean 0% and the document still
    # cites the wrong clause. Only flagged when composite sub-items exist,
    # since a document may legitimately contain several restarting lists.
    total = good + len(bad)
    if total:
        seq = [int(m.group(1))
               for m in (SECTION_RE.match(l) for l in text.splitlines()) if m]
        resets = sum(1 for a, b in zip(seq, seq[1:]) if b <= a)
        if resets:
            print(f"sections: top-level counter resets {resets}x "
                  f"-> {seq[:12]}{'...' if len(seq) > 12 else ''}")
            print("  (verify against .sources/.backup-markitdown/ — if the "
                  "reset is spurious, sub-item numbers inherit the error)")

    if total == 0:
        print("numbering: no hierarchical sub-items found — nothing to check")
        return 0

    pct = 100.0 * len(bad) / total
    print(f"numbering: {good}/{total} sub-items correct, "
          f"{len(bad)} mismatched ({pct:.0f}%)")
    if bad and not args.quiet:
        for n, sect, snippet in bad[:20]:
            print(f"  line {n}: under section {sect} -> {snippet}")
        if len(bad) > 20:
            print(f"  ... and {len(bad) - 20} more")
    return EXIT_BAD if bad else 0


if __name__ == "__main__":
    sys.exit(main())
