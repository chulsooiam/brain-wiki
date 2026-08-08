#!/usr/bin/env python3
"""register-link.py — wikilink known entity/concept pages in register entries.

Makes meeting-note registers first-class citizens of the wiki graph: every
entry links the entities and concepts it touches, so each entity page's
backlinks become its meeting history.

Alias map construction:
  - every wiki page title is its own alias;
  - a parenthetical acronym title "Long Name (ACRO)" also yields aliases
    "Long Name" and "ACRO";
  - optional curated aliases from .vault-meta/register-aliases.json:
      {"aliases": {"alias": "Page Title", ...},
       "exclude": ["alias-to-drop", ...]}
    Curated entries win over derived ones; "exclude" removes derived
    aliases that are too ambiguous in this vault's prose.

Linking rules (precision over recall — a wrong link is worse than a
missing one):
  - whole-word, case-sensitive matches only;
  - longest alias first; overlapping later matches are dropped;
  - never inside existing [[wikilinks]] or `code spans`;
  - at most ONE link per target page per entry (first mention wins);
  - never links a page to itself.

Modes:
  --entries FILE.jsonl   link the named text fields of each JSON object
                         (register-harness format), rewrite in place
  --pages FILE.md ...    link entry sections (### headings) of rendered
                         register pages in place
  --dry-run              report what would be linked, change nothing

Usage:
  python3 scripts/register-link.py --entries backfill_state/entries.jsonl
  python3 scripts/register-link.py --pages "wiki/meetings/Meeting Notes - Others.md" --dry-run
"""
import argparse
import collections
import json
import os
import re
import sys

VAULT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WIKI = os.path.join(VAULT, "wiki")
ALIAS_FILE = os.path.join(VAULT, ".vault-meta", "register-aliases.json")
PAGE_DIRS = ("entities", "concepts")   # link targets
TEXT_FIELDS = ("participants", "agenda", "decisions", "actions", "notable")
PAREN = re.compile(r"^(?P<long>.+?)\s*\((?P<acro>[^)]+)\)$")


def build_alias_map():
    """alias -> canonical page title."""
    aliases = {}
    for d in PAGE_DIRS:
        folder = os.path.join(WIKI, d)
        if not os.path.isdir(folder):
            continue
        for fn in os.listdir(folder):
            if not fn.endswith(".md") or fn.startswith("_"):
                continue
            title = fn[:-3]
            aliases[title] = title
            m = PAREN.match(title)
            if m:
                aliases.setdefault(m.group("long").strip(), title)
                acro = m.group("acro").strip()
                # only acronym-looking parentheticals, not e.g. years
                if re.fullmatch(r"[A-Za-z][A-Za-z0-9 .\-]*", acro):
                    aliases.setdefault(acro, title)
    if os.path.exists(ALIAS_FILE):
        curated = json.load(open(ALIAS_FILE, encoding="utf-8"))
        for a in curated.get("exclude", []):
            aliases.pop(a, None)
        for a, t in curated.get("aliases", {}).items():
            aliases[a] = t
    # drop aliases that can't match as words
    return {a: t for a, t in aliases.items() if re.search(r"\w", a)}


def protected_spans(text):
    """Ranges already inside [[...]] or `...` — never link there."""
    spans = []
    for m in re.finditer(r"\[\[.*?\]\]|`[^`]*`", text):
        spans.append(m.span())
    return spans


def overlaps(span, spans):
    a, b = span
    return any(a < e and s < b for s, e in spans)


def link_text(text, aliases, linked, self_title, report):
    """Link first unlinked mention of each alias; return new text."""
    taken = protected_spans(text)
    repl = []  # (start, end, replacement, title)
    for alias in sorted(aliases, key=len, reverse=True):
        title = aliases[alias]
        if title in linked or title == self_title:
            continue
        pat = re.compile(r"(?<![\w\[])" + re.escape(alias) + r"(?![\w\]])")
        for m in pat.finditer(text):
            if overlaps(m.span(), taken):
                continue
            link = f"[[{title}]]" if alias == title else f"[[{title}|{alias}]]"
            repl.append((m.start(), m.end(), link))
            taken.append(m.span())
            linked.add(title)
            report[title] += 1
            break
    for start, end, link in sorted(repl, reverse=True):
        text = text[:start] + link + text[end:]
    return text


def run_entries(path, aliases, dry):
    rows = [json.loads(l) for l in open(path, encoding="utf-8") if l.strip()]
    report = collections.Counter()
    for r in rows:
        linked = set()
        for f in TEXT_FIELDS:
            if f in r and isinstance(r[f], str):
                r[f] = link_text(r[f], aliases, linked, None, report)
    if not dry:
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            for r in rows:
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    return report, len(rows)


def run_pages(paths, aliases, dry):
    report = collections.Counter()
    n = 0
    for p in paths:
        text = open(p, encoding="utf-8").read()
        self_title = os.path.basename(p)[:-3]
        # link each entry section independently (one link per page per entry)
        parts = re.split(r"(?m)(^### .*$)", text)
        for i in range(2, len(parts), 2):   # bodies following ### headings
            linked = set()
            n += 1
            parts[i] = link_text(parts[i], aliases, linked, self_title, report)
        out = "".join(parts)
        if not dry and out != text:
            open(p, "w", encoding="utf-8", newline="\n").write(out)
    return report, n


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--entries")
    ap.add_argument("--pages", nargs="*")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    aliases = build_alias_map()
    if args.entries:
        report, n = run_entries(args.entries, aliases, args.dry_run)
    elif args.pages:
        report, n = run_pages(args.pages, aliases, args.dry_run)
    else:
        ap.error("need --entries or --pages")
    total = sum(report.values())
    print(f"{'DRY RUN — ' if args.dry_run else ''}{total} links across {n} entries; "
          f"{len(report)} distinct target pages")
    for t, c in report.most_common():
        print(f"  {c:4d}  {t}")


if __name__ == "__main__":
    main()
