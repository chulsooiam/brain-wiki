#!/usr/bin/env python3
"""work-queue.py — maintenance work queue for the reground loop.

wiki-lint *finds* problems (stale claims, lineage gaps, aging contradictions,
unexpanded stubs) but a finding buried in a dated report is where maintenance
goes to die. The work queue closes the loop: lint findings become queue
entries, and ingest sessions drain the queue before taking on new material —
so stale pages re-enter the pipeline as input instead of accumulating.
(Pattern adopted from llm-wiki-newsroom's "reground" loop; see
ATTRIBUTION.md.)

Owns `wiki/meta/work-queue.md` outright: the file is regenerated from parsed
entries on every mutation. Do not hand-edit beyond checking items off in
Obsidian — use `done` so the completion date is recorded.

Entry shape (one line per item):

  - [ ] `stale` [[Page Name]] — detail text (added 2026-08-07)
  - [x] `stub-expand` [[GDI]] — linked from 3 pages, never expanded (added 2026-08-01, done 2026-08-07)

Suggested kinds (free-form lowercase token; these are the conventions):
  stale          claim outdated by a newer source
  lineage        dangling/asymmetric supersession (from lineage-check)
  contradiction  open contradiction needing a decision
  stub-expand    glossary stub with inbound links but no content
  reground       page whose upstream source changed; re-ingest
  defect         bad page found post-ingest (see wiki-lint §Defect Log)
  manual         anything the operator queued by hand

CLI:
  work-queue.py add --kind K --page "Page" --detail "text" [--date YYYY-MM-DD]
  work-queue.py list [--kind K] [--json]
  work-queue.py done --page "Page" [--kind K] [--date YYYY-MM-DD]
  work-queue.py stats

`add` is idempotent per (kind, page): an open entry for the same kind+page is
updated in place (detail refreshed), not duplicated. `done` moves matching
open entries to the Done section. Dates default to today.

Exit codes:
  0 — success
  1 — `done` matched nothing
  2 — usage error
"""

import argparse
import json
import re
import sys
from datetime import date as _date
from pathlib import Path

VAULT_ROOT = Path(__file__).resolve().parent.parent
QUEUE_PATH = VAULT_ROOT / "wiki" / "meta" / "work-queue.md"

ENTRY = re.compile(
    r"^- \[(?P<done>[ xX])\] `(?P<kind>[a-z][\w-]*)` "
    r"\[\[(?P<page>[^\]]+)\]\] — (?P<detail>.*?) "
    r"\(added (?P<added>\d{4}-\d{2}-\d{2})"
    r"(?:, done (?P<done_date>\d{4}-\d{2}-\d{2}))?\)\s*$"
)

HEADER = """---
type: meta
title: "Work Queue"
tags: [meta, work-queue]
---

# Work Queue

Maintenance items awaiting an ingest session. Managed by
`scripts/work-queue.py`; drained per wiki-ingest §Work Queue. Lint emits
into this file (wiki-lint §Lint Checks); do not hand-edit entry lines —
use the script so dates stay accurate.

## Open
"""

EXIT_OK = 0
EXIT_NO_MATCH = 1
EXIT_USAGE = 2


def today():
    return _date.today().isoformat()


def load(path=None):
    p = Path(path) if path else QUEUE_PATH
    entries = []
    if p.is_file():
        for line in p.read_text(encoding="utf-8").splitlines():
            m = ENTRY.match(line.strip())
            if m:
                entries.append({
                    "kind": m.group("kind"),
                    "page": m.group("page"),
                    "detail": m.group("detail"),
                    "added": m.group("added"),
                    "done": m.group("done").lower() == "x",
                    "done_date": m.group("done_date"),
                })
    return entries


def render(entries):
    open_items = [e for e in entries if not e["done"]]
    done_items = [e for e in entries if e["done"]]
    lines = [HEADER.rstrip(), ""]
    for e in sorted(open_items, key=lambda e: (e["added"], e["kind"], e["page"])):
        lines.append(f"- [ ] `{e['kind']}` [[{e['page']}]] — {e['detail']} "
                     f"(added {e['added']})")
    if not open_items:
        lines.append("_Queue is empty._")
    lines += ["", "## Done", ""]
    for e in sorted(done_items,
                    key=lambda e: (e["done_date"] or "", e["added"]),
                    reverse=True):
        done_note = f", done {e['done_date']}" if e["done_date"] else ""
        lines.append(f"- [x] `{e['kind']}` [[{e['page']}]] — {e['detail']} "
                     f"(added {e['added']}{done_note})")
    return "\n".join(lines) + "\n"


def save(entries, path=None):
    p = Path(path) if path else QUEUE_PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(render(entries), encoding="utf-8")


def cmd_add(args):
    entries = load(args.queue)
    kind = args.kind.strip().lower()
    if not re.match(r"^[a-z][\w-]*$", kind):
        print(f"error: kind must be a lowercase token, got {kind!r}",
              file=sys.stderr)
        return EXIT_USAGE
    when = args.date or today()
    for e in entries:
        if not e["done"] and e["kind"] == kind and e["page"] == args.page:
            e["detail"] = args.detail
            save(entries, args.queue)
            print(f"updated open entry: `{kind}` [[{args.page}]]")
            return EXIT_OK
    entries.append({"kind": kind, "page": args.page, "detail": args.detail,
                    "added": when, "done": False, "done_date": None})
    save(entries, args.queue)
    print(f"queued: `{kind}` [[{args.page}]]")
    return EXIT_OK


def cmd_list(args):
    entries = [e for e in load(args.queue) if not e["done"]]
    if args.kind:
        entries = [e for e in entries if e["kind"] == args.kind.lower()]
    if args.json:
        print(json.dumps(entries, indent=2, ensure_ascii=False))
    else:
        for e in entries:
            print(f"`{e['kind']}` [[{e['page']}]] — {e['detail']} "
                  f"(added {e['added']})")
        print(f"{len(entries)} open item(s)", file=sys.stderr)
    return EXIT_OK


def cmd_done(args):
    entries = load(args.queue)
    when = args.date or today()
    matched = 0
    for e in entries:
        if e["done"] or e["page"] != args.page:
            continue
        if args.kind and e["kind"] != args.kind.lower():
            continue
        e["done"] = True
        e["done_date"] = when
        matched += 1
    if matched:
        save(entries, args.queue)
        print(f"done: {matched} entr{'y' if matched == 1 else 'ies'} "
              f"for [[{args.page}]]")
        return EXIT_OK
    print(f"no open entry matched [[{args.page}]]"
          + (f" kind `{args.kind}`" if args.kind else ""), file=sys.stderr)
    return EXIT_NO_MATCH


def cmd_stats(args):
    entries = load(args.queue)
    counts = {}
    for e in entries:
        if not e["done"]:
            counts[e["kind"]] = counts.get(e["kind"], 0) + 1
    print(json.dumps({
        "open": sum(counts.values()),
        "done": sum(1 for e in entries if e["done"]),
        "by_kind": dict(sorted(counts.items(), key=lambda kv: -kv[1])),
    }, indent=2))
    return EXIT_OK


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--queue", help="queue file (default: wiki/meta/work-queue.md)")
    sub = parser.add_subparsers(dest="command", required=True)

    p_add = sub.add_parser("add", help="queue a maintenance item")
    p_add.add_argument("--kind", required=True)
    p_add.add_argument("--page", required=True)
    p_add.add_argument("--detail", required=True)
    p_add.add_argument("--date")
    p_add.set_defaults(func=cmd_add)

    p_list = sub.add_parser("list", help="list open items")
    p_list.add_argument("--kind")
    p_list.add_argument("--json", action="store_true")
    p_list.set_defaults(func=cmd_list)

    p_done = sub.add_parser("done", help="mark items complete")
    p_done.add_argument("--page", required=True)
    p_done.add_argument("--kind")
    p_done.add_argument("--date")
    p_done.set_defaults(func=cmd_done)

    p_stats = sub.add_parser("stats", help="open/done counts per kind")
    p_stats.set_defaults(func=cmd_stats)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
