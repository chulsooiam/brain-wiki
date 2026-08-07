#!/usr/bin/env python3
"""glossary-seed.py — seed entity stubs from an acronym/term table.

Institutional corpora swim in jargon: acronyms, program names, component
codes. If entity pages are invented ad hoc during ingestion, the same term
lands under three spellings and cross-linking degrades. Seeding the glossary
FIRST — before bulk ingestion — gives every subsequent page a stable set of
entity targets to link into.

Input: a term table as .csv/.tsv or .xlsx/.xlsm (first sheet, or --sheet).
Column detection is by header name (case-insensitive):

  term column:        term, acronym, abbreviation, code, name, short
  definition column:  definition, expansion, meaning, description,
                      full form, full_form, long, stands for

…or force with --term-col / --def-col (header name or 0-based index).

Output: one markdown stub per term in --out (default `wiki/entities`),
plus a `glossary.md` index page listing every seeded term. Stubs are
deliberately minimal — a definition and a marker that the page was seeded;
`wiki-ingest` expands them the first time the term is actually discussed
in a source. Existing pages are NEVER overwritten (idempotent; re-running
after new table rows only adds the new terms).

CLI:
  glossary-seed.py FILE [--out DIR] [--sheet NAME]
                   [--term-col X] [--def-col X]
                   [--date YYYY-MM-DD] [--dry-run] [--force]

Exit codes:
  0 — success
  2 — usage error (missing file, undetectable columns)
"""

import argparse
import csv
import importlib.util
import json
import re
import sys
from pathlib import Path

VAULT_ROOT = Path(__file__).resolve().parent.parent

TERM_HEADERS = ("term", "acronym", "abbreviation", "code", "name", "short")
DEF_HEADERS = ("definition", "expansion", "meaning", "description",
               "full form", "full_form", "long", "stands for")

# Characters invalid in filenames on common platforms (and Obsidian).
BAD_FILENAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')

EXIT_OK = 0
EXIT_USAGE = 2


def _load_form_catalogue():
    """Import scripts/form-catalogue.py for its stdlib xlsx reader."""
    spec = importlib.util.spec_from_file_location(
        "form_catalogue", Path(__file__).resolve().parent / "form-catalogue.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def read_table(path, sheet=None):
    """Return rows (list of lists of str) from csv/tsv/xlsx/xlsm."""
    p = Path(path)
    ext = p.suffix.lower()
    if ext in (".csv", ".tsv"):
        delim = "\t" if ext == ".tsv" else ","
        with open(p, newline="", encoding="utf-8-sig", errors="replace") as fh:
            return list(csv.reader(fh, delimiter=delim))
    if ext in (".xlsx", ".xlsm"):
        sheets = _load_form_catalogue().read_xlsx(p)
        if not sheets:
            raise SystemExit(f"error: no sheets in {p}")
        if sheet:
            for name in sheets:
                if name.strip().lower() == sheet.strip().lower():
                    return sheets[name]
            raise SystemExit(f"error: no sheet named {sheet!r} in {p} "
                             f"(has: {', '.join(sheets)})")
        return next(iter(sheets.values()))
    raise SystemExit(f"error: unsupported table format {ext} "
                     "(use .csv, .tsv, .xlsx, or .xlsm)")


def resolve_col(spec_value, headers, candidates, label):
    """--term-col/--def-col override (name or index), else header detection."""
    if spec_value is not None:
        if spec_value.isdigit():
            return int(spec_value)
        for i, h in enumerate(headers):
            if h.strip().lower() == spec_value.strip().lower():
                return i
        raise SystemExit(f"error: no column {spec_value!r} for {label} "
                         f"(headers: {', '.join(h for h in headers if h)})")
    for i, h in enumerate(headers):
        if h.strip().lower() in candidates:
            return i
    raise SystemExit(
        f"error: could not detect the {label} column "
        f"(headers: {', '.join(h for h in headers if h)}); use --{label}-col")


def safe_filename(term):
    cleaned = BAD_FILENAME.sub("-", term).strip().strip(".")
    if not any(ch.isalnum() for ch in cleaned):
        return "unnamed-term"
    return cleaned


def stub_content(term, definition, source_name, date=None):
    fm = ["---", 'type: entity', "entity_type: term"]
    if date:
        fm.append(f"created: {date}")
    fm += ["glossary_seed: true", "tags: [glossary]", "---"]
    body = [
        "",
        f"# {term}",
        "",
        definition if definition else "_No definition provided in the seed table._",
        "",
        f"> [!note] Seeded from `{source_name}`",
        "> Stub page — expand when this term is first discussed in a source.",
        "",
    ]
    return "\n".join(fm + body)


def seed(rows, out_dir, source_name, term_col, def_col, date=None,
         dry_run=False, force=False):
    """Returns (created, skipped, terms) — terms is [(term, definition), ...]."""
    created, skipped, terms = [], [], []
    seen = set()
    for row in rows[1:]:
        term = row[term_col].strip() if len(row) > term_col else ""
        definition = row[def_col].strip() if len(row) > def_col else ""
        if not term or term.lower() in seen:
            continue
        seen.add(term.lower())
        terms.append((term, definition))
        target = out_dir / f"{safe_filename(term)}.md"
        if target.exists() and not force:
            skipped.append(term)
            continue
        if not dry_run:
            out_dir.mkdir(parents=True, exist_ok=True)
            target.write_text(stub_content(term, definition, source_name, date),
                              encoding="utf-8")
        created.append(term)
    return created, skipped, terms


def glossary_index(terms, source_name, date=None):
    fm = ["---", "type: meta", 'title: "Glossary"']
    if date:
        fm.append(f"updated: {date}")
    fm += ["tags: [meta, glossary]", "---", "", "# Glossary", "",
           f"Seeded from `{source_name}`. One line per term; each links to "
           "its entity stub.", ""]
    lines = fm
    for term, definition in sorted(terms, key=lambda t: t[0].lower()):
        d = f" — {definition}" if definition else ""
        lines.append(f"- [[{safe_filename(term)}|{term}]]{d}")
    return "\n".join(lines) + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("file", help="term table (.csv/.tsv/.xlsx/.xlsm)")
    parser.add_argument("--out", default=str(VAULT_ROOT / "wiki" / "entities"))
    parser.add_argument("--sheet", help="sheet name for workbooks")
    parser.add_argument("--term-col", help="term column (header or 0-based index)")
    parser.add_argument("--def-col", help="definition column (header or index)")
    parser.add_argument("--date", help="YYYY-MM-DD for created:/updated: fields")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force", action="store_true",
                        help="overwrite existing stubs (default: never)")
    args = parser.parse_args(argv)

    src = Path(args.file)
    if not src.is_file():
        print(f"error: no such file: {src}", file=sys.stderr)
        return EXIT_USAGE
    try:
        rows = read_table(src, args.sheet)
        if not rows:
            print("error: empty table", file=sys.stderr)
            return EXIT_USAGE
        headers = rows[0]
        term_col = resolve_col(args.term_col, headers, TERM_HEADERS, "term")
        def_col = resolve_col(args.def_col, headers, DEF_HEADERS, "def")
    except SystemExit as exc:
        if exc.code and not isinstance(exc.code, int):
            print(exc.code, file=sys.stderr)
            return EXIT_USAGE
        raise

    out_dir = Path(args.out)
    created, skipped, terms = seed(
        rows, out_dir, src.name, term_col, def_col,
        date=args.date, dry_run=args.dry_run, force=args.force)

    index_path = out_dir / "glossary.md"
    if not args.dry_run and terms:
        out_dir.mkdir(parents=True, exist_ok=True)
        index_path.write_text(glossary_index(terms, src.name, args.date),
                              encoding="utf-8")

    print(json.dumps({
        "created": len(created),
        "skipped_existing": len(skipped),
        "terms_total": len(terms),
        "index": str(index_path),
        "dry_run": args.dry_run,
    }, indent=2))
    if args.dry_run:
        for t in created[:20]:
            print(f"  would create: {t}", file=sys.stderr)
        if len(created) > 20:
            print(f"  … and {len(created) - 20} more", file=sys.stderr)
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
