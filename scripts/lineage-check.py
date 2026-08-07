#!/usr/bin/env python3
"""lineage-check.py — deterministic version-lineage validator for wiki pages.

Institutional corpora are full of documents that replace each other: policy
instruction revisions, framework editions, `LEGACY_`-prefixed exports, v1/v2
form templates. A wiki that flattens these into sibling pages will happily
retrieve the 2017 revision of a policy as if it were current. The lineage
convention makes supersession explicit in frontmatter:

    ---
    title: Data Governance Policy (Rev 1)
    supersedes: "[[Data Governance Policy (2017)]]"
    superseded_by: "[[Data Governance Policy (Rev 2 draft)]]"
    ---

`supersedes` / `superseded_by` each accept a single wikilink or a list
(inline `["[[A]]", "[[B]]"]` or block `- "[[A]]"` form). A page with no
`superseded_by` is a **head** — the current version of its chain.

This script walks `wiki/**/*.md`, builds the lineage graph, and reports:

  dangling    — a lineage field points at a page that does not exist
  asymmetry   — A supersedes B, but B does not declare superseded_by A
                (or vice versa); includes the exact fix
  cycle       — supersession loop (A→B→…→A); no chain head exists
  multi-head ambiguity is NOT an error: two pages may both supersede a
  common ancestor (a fork); the chains listing makes forks visible.

Consumed by skills/wiki-lint (severity mapping lives there) and usable
directly. Read-only: never modifies pages.

CLI:
  lineage-check.py check [--json] [--root DIR]   # findings; exit 1 if any
  lineage-check.py chains [--json] [--root DIR]  # lineage chains, heads first

Exit codes:
  0 — no findings (check) / success (chains)
  1 — findings present (check)
  2 — usage error
"""

import argparse
import json
import re
import sys
from pathlib import Path

VAULT_ROOT = Path(__file__).resolve().parent.parent

WIKILINK = re.compile(r"\[\[([^\]|#]+)(?:[#|][^\]]*)?\]\]")
LINEAGE_KEYS = ("supersedes", "superseded_by")

EXIT_OK = 0
EXIT_FINDINGS = 1
EXIT_USAGE = 2


def parse_frontmatter(text):
    """Minimal YAML-subset frontmatter parser: `key: value` scalars and
    block lists (`- item`). Returns {} when no frontmatter fence."""
    if not text.startswith("---"):
        return {}
    lines = text.splitlines()
    fm = {}
    key = None
    for line in lines[1:]:
        if line.strip() == "---":
            break
        if re.match(r"^[A-Za-z_][\w-]*\s*:", line):
            key, _, value = line.partition(":")
            key = key.strip()
            value = value.strip()
            if value:
                fm[key] = value
                key = None
            else:
                fm[key] = []
        elif key is not None and line.strip().startswith("- "):
            fm[key].append(line.strip()[2:].strip())
    return fm


def extract_targets(value):
    """Lineage field value → list of page names. Accepts a wikilink string,
    a quoted wikilink, an inline list, or a block list (already a list)."""
    if isinstance(value, list):
        items = value
    elif value.startswith("[") and not value.startswith("[["):
        items = [v.strip() for v in value[1:-1].split(",")]
    else:
        items = [value]
    targets = []
    for item in items:
        item = item.strip().strip("'\"")
        m = WIKILINK.search(item)
        if m:
            targets.append(m.group(1).strip())
        elif item:
            targets.append(item)
    return targets


def scan(root):
    """Return {page_name: {"path", "supersedes": [...], "superseded_by": [...]}}.
    page_name is the filename stem (the wikilink target form)."""
    pages = {}
    for path in sorted(root.rglob("*.md")):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        fm = parse_frontmatter(text)
        entry = {"path": path, "supersedes": [], "superseded_by": []}
        for lk in LINEAGE_KEYS:
            if lk in fm:
                entry[lk] = extract_targets(fm[lk])
        pages[path.stem] = entry
    return pages


def check(pages):
    findings = []

    def exists(name):
        return name in pages

    for name, entry in sorted(pages.items()):
        rel = entry["path"]
        for lk, inverse in (("supersedes", "superseded_by"),
                            ("superseded_by", "supersedes")):
            for target in entry[lk]:
                if not exists(target):
                    findings.append({
                        "kind": "dangling",
                        "page": name,
                        "field": lk,
                        "target": target,
                        "detail": f"{rel.name}: {lk} → [[{target}]] does not exist",
                    })
                elif name not in pages[target][inverse]:
                    findings.append({
                        "kind": "asymmetry",
                        "page": name,
                        "field": lk,
                        "target": target,
                        "detail": (f"{rel.name}: {lk} → [[{target}]], but "
                                   f"'{target}' lacks {inverse}: \"[[{name}]]\""),
                        "fix": f"add `{inverse}: \"[[{name}]]\"` to "
                               f"{pages[target]['path'].name}",
                    })

    # Cycle detection over the supersedes edges (A supersedes B: edge A→B).
    WHITE, GRAY, BLACK = 0, 1, 2
    color = {n: WHITE for n in pages}

    def dfs(node, stack):
        color[node] = GRAY
        stack.append(node)
        for nxt in pages[node]["supersedes"]:
            if nxt not in pages:
                continue
            if color[nxt] == GRAY:
                cycle = stack[stack.index(nxt):] + [nxt]
                findings.append({
                    "kind": "cycle",
                    "page": node,
                    "detail": "supersession cycle: " + " → ".join(cycle),
                })
            elif color[nxt] == WHITE:
                dfs(nxt, stack)
        stack.pop()
        color[node] = BLACK

    for n in sorted(pages):
        if color[n] == WHITE:
            dfs(n, [])
    return findings


def chains(pages):
    """Return lineage chains as lists of page names, head (current) first.
    Only pages participating in lineage appear."""
    involved = {n for n, e in pages.items()
                if e["supersedes"] or e["superseded_by"]}
    heads = sorted(n for n in involved if not pages[n]["superseded_by"])
    out = []
    for head in heads:
        chain, seen = [], set()
        frontier = [head]
        while frontier:
            cur = frontier.pop(0)
            if cur in seen or cur not in pages:
                continue
            seen.add(cur)
            chain.append(cur)
            frontier.extend(t for t in pages[cur]["supersedes"] if t in pages)
        out.append(chain)
    return out


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", help="wiki root (default: <vault>/wiki)")
    sub = parser.add_subparsers(dest="command", required=True)
    p_check = sub.add_parser("check", help="report lineage findings")
    p_check.add_argument("--json", action="store_true")
    p_chains = sub.add_parser("chains", help="list lineage chains")
    p_chains.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    root = Path(args.root) if args.root else VAULT_ROOT / "wiki"
    if not root.is_dir():
        print(f"error: no such directory: {root}", file=sys.stderr)
        return EXIT_USAGE
    pages = scan(root)

    if args.command == "check":
        findings = check(pages)
        if args.json:
            print(json.dumps(findings, indent=2, ensure_ascii=False, default=str))
        else:
            for f in findings:
                line = f"[{f['kind']}] {f['detail']}"
                if "fix" in f:
                    line += f"  (fix: {f['fix']})"
                print(line)
            print(f"{len(findings)} finding(s) across {len(pages)} page(s)",
                  file=sys.stderr)
        return EXIT_FINDINGS if findings else EXIT_OK

    result = chains(pages)
    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        for chain in result:
            print("  →  ".join(chain) if len(chain) > 1 else chain[0])
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
