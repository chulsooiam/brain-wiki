#!/usr/bin/env python3
"""register-actions.py — roll up one owner's action points from the registers.

Parses the rendered meeting-note register pages (not harness state, so it
works for entries added by any route), extracts every action point owned
by the configured owner, and rewrites the Action Points page: newest
first, grouped by register, one checkbox per action, each linking back to
its source meeting entry.

Owner matching: an action segment is "Owner → action". The owner side
matches if it contains any name in OWNER_NAMES (word-boundary) or a
wikilink to the owner's page. Joint owners ("Alice and Bob → ...")
count. Deliberately narrow: "team", "all staff" and unnamed speakers are
not the owner's personal actions.

Checkbox states are PRESERVED across rebuilds: an action already ticked
`- [x]` on the existing page stays ticked when the page is regenerated
(matched by source meeting + action text).

Usage:
  python3 scripts/register-actions.py            # rebuild the page
  python3 scripts/register-actions.py --dry-run  # print what would change
"""
import argparse
import json
import os
import re

VAULT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MEETINGS = os.path.join(VAULT, "wiki", "meetings")
OUT = os.path.join(MEETINGS, "Action Points.md")
CONFIG = os.path.join(VAULT, ".vault-meta", "register-actions.json")

# generic defaults; set the real owner in .vault-meta/register-actions.json
OWNER_PAGE = None      # entity page title of the owner
OWNER_NAMES = []       # names that identify the owner on an action's owner side
REGISTERS = None       # None -> auto-discover wiki/meetings/*.md

ENTRY = re.compile(r"(?m)^### (?P<head>(?P<date>\d{4}-\d{2}-\d{2}) [—–-] (?P<name>.+?))\s*$")
ACTIONS_LINE = re.compile(r"(?m)^- \*\*Action points:\*\* (?P<body>.+)$")


def load_config():
    global OWNER_PAGE, OWNER_NAMES, REGISTERS
    if os.path.exists(CONFIG):
        cfg = json.load(open(CONFIG, encoding="utf-8"))
        OWNER_PAGE = cfg.get("owner_page", OWNER_PAGE)
        OWNER_NAMES = cfg.get("owner_names", OWNER_NAMES)
        REGISTERS = cfg.get("registers", REGISTERS)
    if not OWNER_PAGE or not OWNER_NAMES:
        raise SystemExit(
            "no owner configured — set owner_page and owner_names in "
            + CONFIG)
    if REGISTERS is None:
        REGISTERS = sorted(
            f[:-3] for f in os.listdir(MEETINGS)
            if f.endswith(".md") and not f.startswith("_")
            and f != os.path.basename(OUT))


def owner_matches(owner_side):
    if f"[[{OWNER_PAGE}" in owner_side:
        return True
    plain = re.sub(r"\[\[[^\]]*\]\]", " ", owner_side)
    return any(re.search(r"\b" + re.escape(n) + r"\b", plain) for n in OWNER_NAMES)


def split_segments(body):
    """Split an action-points string into Owner → action segments."""
    # segment boundaries: '; ' always; '. ' only when what follows looks
    # like a new "Owner →" clause (avoids splitting inside one action)
    parts = re.split(r";\s+|(?<=\.)\s+(?=[^.;]{0,80}?→)", body)
    return [p.strip(" .") for p in parts if "→" in p]


def collect():
    items = []
    for reg in REGISTERS:
        path = os.path.join(MEETINGS, reg + ".md")
        if not os.path.exists(path):
            continue
        text = open(path, encoding="utf-8").read()
        entries = list(ENTRY.finditer(text))
        for i, m in enumerate(entries):
            end = entries[i + 1].start() if i + 1 < len(entries) else len(text)
            block = text[m.start():end]
            am = ACTIONS_LINE.search(block)
            if not am or am.group("body").strip().startswith("None recorded"):
                continue
            for seg in split_segments(am.group("body")):
                owner, _, action = seg.partition("→")
                if owner_matches(owner):
                    items.append({
                        "register": reg,
                        "date": m.group("date"),
                        "meeting": m.group("name").strip(),
                        "head": m.group("head"),
                        "action": action.strip(),
                    })
    return items


def existing_ticks():
    """(anchor, action) pairs already checked off on the current page."""
    ticked = set()
    if os.path.exists(OUT):
        for line in open(OUT, encoding="utf-8"):
            cm = re.match(r"- \[x\] \*\*(\d{4}-\d{2}-\d{2})\*\* — (.+?) _\(\[\[", line)
            if cm:
                ticked.add((cm.group(1), cm.group(2).strip()))
    return ticked


def render(items, ticked):
    from datetime import date
    today = date.today().isoformat()
    lines = [
        "---",
        "type: meta",
        'title: "Action Points"',
        'tier: "1"',
        "confidence: high",
        f"updated: {today}",
        "tags: [meta, meeting-notes, actions]",
        "related: " + json.dumps([f"[[{r}]]" for r in REGISTERS]),
        "---",
        "",
        "# Action Points",
        "",
        f"Every action point owned by [[{OWNER_PAGE}]] across the four",
        "meeting-note registers, newest first. Regenerated by",
        "`scripts/register-actions.py` — **checked boxes survive the",
        "rebuild**, so tick items off freely. Actions are as recorded in",
        "the meeting; many from the backfilled archive will already be",
        "done — ticking them is the triage.",
        "",
    ]
    for reg in REGISTERS:
        regitems = [i for i in items if i["register"] == reg]
        if not regitems:
            continue
        lines.append(f"## [[{reg}]] ({len(regitems)})")
        lines.append("")
        for i in sorted(regitems, key=lambda x: x["date"], reverse=True):
            box = "x" if (i["date"], i["action"]) in ticked else " "
            anchor = f"{i['register']}#{i['head']}"
            lines.append(
                f"- [{box}] **{i['date']}** — {i['action']} _([[{anchor}|source]])_")
        lines.append("")
    return "\n".join(lines) + "\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    load_config()
    items = collect()
    out = render(items, existing_ticks())
    if args.dry_run:
        print(f"DRY RUN — {len(items)} actions")
    else:
        open(OUT, "w", encoding="utf-8", newline="\n").write(out)
        print(f"wrote {OUT}: {len(items)} actions")
    per = {}
    for i in items:
        per[i["register"]] = per.get(i["register"], 0) + 1
    for r, c in per.items():
        print(f"  {c:4d}  {r}")


if __name__ == "__main__":
    main()
