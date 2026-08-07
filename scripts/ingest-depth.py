#!/usr/bin/env python3
"""ingest-depth.py — tiered ingestion-depth planner for heterogeneous corpora.

Real institutional corpora are not uniform: a 3 KB policy memo, an 850-page
textbook PDF, a 50 MB slide deck, and a 5,000-row survey spreadsheet do not
deserve the same ingestion treatment. Feeding everything through full-fidelity
`wiki-ingest` drowns signal in bulk and burns tokens on low-value pages.

This script maintains a per-file **ingestion plan** (`.raw/.ingest-plan.json`)
that assigns each source one of four depths:

  full       — complete wiki-ingest treatment (source page, entities,
               concepts, cross-references). The default.
  summary    — one source page holding a structured summary + section/chapter
               index; entity pages only for principals. For reference books,
               annual reports, large slide decks.
  catalogue  — a registry entry only; content is NOT ingested as prose.
               For spreadsheets, survey forms, datasets (see
               skills/form-catalogue for the spreadsheet-specific treatment).
  skip       — not wiki material (OS junk, empty stubs).

Depth is proposed by deterministic heuristics (extension class + size),
recorded with a reason, and always overridable by the operator. Overrides
survive re-assignment. `wiki-ingest` consults the plan before processing;
a missing plan or unlisted file means `full` — fully backward compatible.

CLI:
  ingest-depth.py assign PATH... [--large-bytes N] [--dry-run]
  ingest-depth.py get FILE            # print effective depth for one file
  ingest-depth.py set FILE DEPTH [--reason TEXT]
  ingest-depth.py list [--depth DEPTH]
  ingest-depth.py summary

`assign` walks files/directories, proposes depths for files not yet planned
(or previously auto-assigned), merges into the plan, and writes it. Entries
with `"override": true` are never touched. Paths are stored vault-relative
when under the vault root, else absolute.

Heuristics (in order; first match wins):
  os-junk        basename in {desktop.ini, thumbs.db, .ds_store}  → skip
  structured     .xlsx .xls .xlsm .csv .tsv .ods                  → catalogue
  slide-deck     .pptx .ppt .odp                                  → summary
  large-document text/document formats above --large-bytes
                 (default 2,000,000)                              → summary
  document       text/document formats at or below the threshold  → full
  default        anything else (incl. images: vision-described)   → full

Exit codes:
  0 — success
  2 — usage error (unknown depth, missing file, bad path)
  3 — plan file unreadable/corrupt
"""

import argparse
import json
import sys
import tempfile
from pathlib import Path

VAULT_ROOT = Path(__file__).resolve().parent.parent
PLAN_PATH = VAULT_ROOT / ".raw" / ".ingest-plan.json"

DEPTHS = ("full", "summary", "catalogue", "skip")
DEFAULT_LARGE_BYTES = 2_000_000

OS_JUNK = {"desktop.ini", "thumbs.db", ".ds_store"}
STRUCTURED_EXTS = {".xlsx", ".xls", ".xlsm", ".csv", ".tsv", ".ods"}
DECK_EXTS = {".pptx", ".ppt", ".odp"}
DOCUMENT_EXTS = {".pdf", ".docx", ".doc", ".md", ".txt", ".rtf", ".odt",
                 ".html", ".htm"}

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_CORRUPT = 3


def propose_depth(path, size, large_bytes=DEFAULT_LARGE_BYTES):
    """Return (depth, reason) for a file. Pure function; heuristics only."""
    name = Path(path).name.lower()
    ext = Path(path).suffix.lower()
    if name in OS_JUNK:
        return "skip", "os-junk"
    if ext in STRUCTURED_EXTS:
        return "catalogue", "structured-data"
    if ext in DECK_EXTS:
        return "summary", "slide-deck"
    if ext in DOCUMENT_EXTS:
        if size > large_bytes:
            return "summary", f"large-document (> {large_bytes} bytes)"
        return "full", "document"
    return "full", "default"


def rel_key(path):
    """Vault-relative key when inside the vault, else absolute. Forward slashes."""
    p = Path(path).resolve()
    try:
        return p.relative_to(VAULT_ROOT).as_posix()
    except ValueError:
        return p.as_posix()


def load_plan(plan_path=None):
    p = Path(plan_path) if plan_path else PLAN_PATH
    if not p.is_file():
        return {"schema_version": 1, "entries": {}}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(data.get("entries"), dict):
            raise ValueError("missing 'entries' object")
        return data
    except (json.JSONDecodeError, ValueError) as exc:
        raise SystemExit(f"error: corrupt plan {p}: {exc}") from exc


def save_plan(plan, plan_path=None):
    p = Path(plan_path) if plan_path else PLAN_PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    # Atomic-ish write: temp file in the same directory, then replace.
    with tempfile.NamedTemporaryFile("w", dir=p.parent, suffix=".tmp",
                                     delete=False, encoding="utf-8") as tmp:
        json.dump(plan, tmp, indent=2, ensure_ascii=False, sort_keys=True)
        tmp.write("\n")
        tmp_path = Path(tmp.name)
    tmp_path.replace(p)


def iter_files(paths):
    for raw in paths:
        p = Path(raw)
        if p.is_dir():
            yield from sorted(q for q in p.rglob("*") if q.is_file())
        elif p.is_file():
            yield p
        else:
            raise SystemExit(f"error: no such file or directory: {raw}")


def cmd_assign(args):
    plan = load_plan(args.plan)
    entries = plan["entries"]
    proposed, kept_overrides = 0, 0
    for f in iter_files(args.paths):
        key = rel_key(f)
        existing = entries.get(key)
        if existing and existing.get("override"):
            kept_overrides += 1
            continue
        depth, reason = propose_depth(f, f.stat().st_size, args.large_bytes)
        entries[key] = {"depth": depth, "reason": reason, "override": False}
        proposed += 1
    if args.dry_run:
        print(json.dumps(plan, indent=2, ensure_ascii=False, sort_keys=True))
    else:
        save_plan(plan, args.plan)
    print(f"assigned: {proposed}  overrides-preserved: {kept_overrides}"
          f"  total-planned: {len(entries)}", file=sys.stderr)
    return EXIT_OK


def cmd_get(args):
    plan = load_plan(args.plan)
    entry = plan["entries"].get(rel_key(args.file))
    print(entry["depth"] if entry else "full")
    return EXIT_OK


def cmd_set(args):
    if args.depth not in DEPTHS:
        print(f"error: depth must be one of {', '.join(DEPTHS)}", file=sys.stderr)
        return EXIT_USAGE
    plan = load_plan(args.plan)
    plan["entries"][rel_key(args.file)] = {
        "depth": args.depth,
        "reason": args.reason or "operator override",
        "override": True,
    }
    save_plan(plan, args.plan)
    print(f"{rel_key(args.file)} -> {args.depth} (override)")
    return EXIT_OK


def cmd_list(args):
    plan = load_plan(args.plan)
    entries = plan["entries"]
    if args.depth:
        entries = {k: v for k, v in entries.items() if v["depth"] == args.depth}
    print(json.dumps(entries, indent=2, ensure_ascii=False, sort_keys=True))
    return EXIT_OK


def cmd_summary(args):
    plan = load_plan(args.plan)
    counts = {d: 0 for d in DEPTHS}
    overrides = 0
    for v in plan["entries"].values():
        counts[v["depth"]] = counts.get(v["depth"], 0) + 1
        if v.get("override"):
            overrides += 1
    counts["total"] = len(plan["entries"])
    counts["overrides"] = overrides
    print(json.dumps(counts, indent=2))
    return EXIT_OK


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--plan", help="plan file (default: .raw/.ingest-plan.json)")
    sub = parser.add_subparsers(dest="command", required=True)

    p_assign = sub.add_parser("assign", help="propose depths for files/dirs")
    p_assign.add_argument("paths", nargs="+")
    p_assign.add_argument("--large-bytes", type=int, default=DEFAULT_LARGE_BYTES)
    p_assign.add_argument("--dry-run", action="store_true")
    p_assign.set_defaults(func=cmd_assign)

    p_get = sub.add_parser("get", help="effective depth for one file")
    p_get.add_argument("file")
    p_get.set_defaults(func=cmd_get)

    p_set = sub.add_parser("set", help="operator depth override")
    p_set.add_argument("file")
    p_set.add_argument("depth")
    p_set.add_argument("--reason")
    p_set.set_defaults(func=cmd_set)

    p_list = sub.add_parser("list", help="list planned entries")
    p_list.add_argument("--depth", choices=DEPTHS)
    p_list.set_defaults(func=cmd_list)

    p_sum = sub.add_parser("summary", help="counts per depth")
    p_sum.set_defaults(func=cmd_summary)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
