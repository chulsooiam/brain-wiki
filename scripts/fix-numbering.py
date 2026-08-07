#!/usr/bin/env python3
"""Repair fabricated clause numbers in docling DOCX output (2026-07-27).

Companion to check-numbering.py. Docling reconstructs Word auto-numbering as
literal text and gets the leading component wrong. Comparing against the
markitdown backups (which render the same lists as plain nested ordered lists)
showed the damage comes in two distinct shapes, which need different repairs:

  --mode section
      The sub-items DO sit under a genuine top-level numbered section, but
      docling restarts the leading component at 1 for every section:
      `1.1.`, `1.2.1.` appearing under `2. Public Access`. The sibling
      sequence is correct; only the leading component is wrong. Rewrite it to
      the enclosing section number, at any depth. This restores the numbering
      Word actually displays, so `2.2.4.` again means clause 2.2.4.

  --mode strip
      There is no numbered parent at all — the parent is a bullet, and the
      whole composite number is invented. Citing "clause 1.3" here would cite
      something that does not exist in the source. Drop the fabricated leading
      component and emit a plain ordered list, matching what markitdown
      produced: `- 1.4. Text` -> `4. Text`.

  --mode drop
      Every component is fabricated, so nothing can be salvaged: `0.1.1.2.1.`,
      or `2.4.` where the source item is plainly the first in its list. Remove
      the number entirely and keep the bullet — indentation already carries
      the nesting. Invents nothing; only deletes what docling made up.

Choosing the wrong mode silently produces plausible-looking but wrong clause
numbers, so mode is required and never inferred. Confirm against the
markitdown backup in .sources/.backup-markitdown/, which renders these lists
as plain nested ordered lists and so shows the true sequence. Verify with
check-numbering.py afterwards, and diff against the pre-fix backup.

Dry-run unless --apply is passed.

Usage:
    fix-numbering.py --mode {section,strip,drop} [--apply]
                     [--backup-dir DIR] FILE...

Exit codes:
  0 — completed (dry-run or applied)
  2 — usage error
"""
import argparse
import io
import os
import re
import shutil
import sys

EXIT_USAGE = 2

# "2. **Public Access**" / "2. Public Access" at column 0 — a real section.
SECTION_RE = re.compile(r"^(\d+)\.\s+\*{0,2}\S")
# "    - 1.2.1. Text" — docling renders numbered sub-items as bullets.
COMPOSITE_RE = re.compile(r"^(\s+[-*]\s+)(\d+)((?:\.\d+)+)\.(\s)")
# Same, restricted to two levels, capturing indent and the true ordinal.
TWO_LEVEL_RE = re.compile(r"^(\s+)[-*]\s+(\d+)\.(\d+)\.(\s)")


def wp(path):
    """Windows extended-length path: these corpora exceed MAX_PATH."""
    if os.name == "nt":
        ap = os.path.abspath(path)
        if not ap.startswith("\\\\?\\"):
            return "\\\\?\\" + ap
        return ap
    return path


def fix_section(lines):
    """Rewrite each composite's leading component to its enclosing section."""
    section = None
    out, changes = [], []
    for n, line in enumerate(lines, 1):
        m = SECTION_RE.match(line)
        if m:
            section = m.group(1)
            out.append(line)
            continue
        m2 = COMPOSITE_RE.match(line)
        if m2 and section and m2.group(2) != section:
            new = COMPOSITE_RE.sub(
                lambda g: g.group(1) + section + g.group(3) + "." + g.group(4),
                line, count=1)
            changes.append((n, line.strip()[:70], new.strip()[:70]))
            out.append(new)
        else:
            out.append(line)
    return out, changes


def fix_drop(lines):
    """Delete the fabricated number outright, keeping the bullet and indent."""
    out, changes = [], []
    for n, line in enumerate(lines, 1):
        m = COMPOSITE_RE.match(line)
        if m:
            new = COMPOSITE_RE.sub(r"\1", line, count=1)
            changes.append((n, line.strip()[:70], new.strip()[:70]))
            out.append(new)
        else:
            out.append(line)
    return out, changes


def fix_strip(lines):
    """Drop the invented leading component, leaving a plain ordered list."""
    out, changes = [], []
    for n, line in enumerate(lines, 1):
        m = TWO_LEVEL_RE.match(line)
        if m:
            new = TWO_LEVEL_RE.sub(r"\1\3.\4", line, count=1)
            changes.append((n, line.strip()[:70], new.strip()[:70]))
            out.append(new)
        else:
            out.append(line)
    return out, changes


def main():
    ap = argparse.ArgumentParser(description="Repair docling clause numbering.")
    ap.add_argument("--mode", required=True,
                    choices=["section", "strip", "drop"])
    ap.add_argument("--apply", action="store_true",
                    help="Write changes (default is dry-run)")
    ap.add_argument("--backup-dir",
                    help="Copy each file here before writing")
    ap.add_argument("--only-lines",
                    help="Comma-separated 1-based line numbers to touch. For "
                         "files that mix correct and fabricated numbering — "
                         "CDD Documentation had 37 correct clause numbers "
                         "alongside 12 fabricated ones, and a whole-file pass "
                         "would have destroyed the correct ones.")
    ap.add_argument("paths", nargs="+")
    args = ap.parse_args()

    total = 0
    for path in args.paths:
        try:
            text = io.open(wp(path), encoding="utf-8").read()
        except OSError as e:
            print(f"ERR: {path}: {e}", file=sys.stderr)
            return EXIT_USAGE

        # splitlines()/join loses a trailing newline; preserve it explicitly.
        trailing = "\n" if text.endswith("\n") else ""
        lines = text.splitlines()
        fixer = {"section": fix_section,
                 "strip": fix_strip,
                 "drop": fix_drop}[args.mode]
        out, changes = fixer(lines)

        if args.only_lines:
            keep = {int(x) for x in args.only_lines.split(",") if x.strip()}
            out = [out[i] if (i + 1) in keep else lines[i]
                   for i in range(len(lines))]
            changes = [c for c in changes if c[0] in keep]
            missed = keep - {c[0] for c in changes}
            if missed:
                print(f"   WARN: no change produced on line(s) "
                      f"{sorted(missed)} — check the mode", file=sys.stderr)

        print(f"{'APPLY' if args.apply else 'DRY '} {os.path.basename(path)}: "
              f"{len(changes)} line(s)")
        for n, before, after in changes[:6]:
            print(f"   L{n}: {before}")
            print(f"      -> {after}")
        if len(changes) > 6:
            print(f"   ... and {len(changes) - 6} more")
        total += len(changes)

        if args.apply and changes:
            if args.backup_dir:
                dest = os.path.join(args.backup_dir, os.path.basename(path))
                os.makedirs(wp(args.backup_dir), exist_ok=True)
                shutil.copy2(wp(path), wp(dest))
            with io.open(wp(path), "w", encoding="utf-8", newline="\n") as fh:
                fh.write("\n".join(out) + trailing)

    print(f"total: {total} line(s) {'changed' if args.apply else 'would change'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
