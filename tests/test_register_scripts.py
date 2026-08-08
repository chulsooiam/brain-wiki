#!/usr/bin/env python3
"""Tests for register-link.py and register-actions.py.

Codifies the adversarial findings from the 2026-08-08 hardening pass:
  - link pass must be idempotent (re-running adds zero links);
  - pre-existing [[links]] count toward the one-link-per-entry budget;
  - code fences are never linked into, even when they contain ###
    headings (the entry template quoted on a conventions page);
  - inline code and existing wikilinks are protected;
  - pages never link to themselves;
  - actions: entry headings match em-dash, en-dash and hyphen (a hyphen
    used to drop the entry's actions silently);
  - actions: source anchors use the heading verbatim, so the link
    resolves whatever dash the heading used;
  - actions: checked boxes survive a rebuild; unconfigured owner is a
    loud error, not empty output.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = sys.executable
FAILURES = []


def check(name, cond, detail=""):
    if cond:
        print(f"  ok  {name}")
    else:
        print(f"FAIL  {name}  {detail}")
        FAILURES.append(name)


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


def run(script, *args, cwd):
    return subprocess.run([PY, os.path.join("scripts", script), *args],
                          cwd=cwd, capture_output=True, text=True)


def make_vault(tmp):
    for d in ("scripts", "wiki/entities", "wiki/concepts", "wiki/meetings",
              ".vault-meta"):
        os.makedirs(os.path.join(tmp, d), exist_ok=True)
    for s in ("register-link.py", "register-actions.py"):
        shutil.copy(os.path.join(ROOT, "scripts", s),
                    os.path.join(tmp, "scripts", s))
    write(os.path.join(tmp, "wiki/entities/Alice Smith (CEO).md"), "# a\n")
    write(os.path.join(tmp, "wiki/entities/Bob Jones.md"), "# b\n")
    write(os.path.join(tmp, "wiki/concepts/Data Platform (DP).md"), "# c\n")
    write(os.path.join(tmp, "wiki/concepts/Data Platform Toolkit.md"), "# d\n")
    write(os.path.join(tmp, ".vault-meta/register-aliases.json"),
          json.dumps({"aliases": {"Bob": "Bob Jones"}, "exclude": []}))


PAGE = """---
type: source
---
# Meeting Notes - Test

## Conventions

```markdown
### YYYY-MM-DD — <Meeting Name>
- **Participants:** Bob Jones and Data Platform Toolkit inside fence
```

## Meetings

### 2026-08-01 — Platform sync

- **Participants:** Alice Smith, Bob, and the DP team. Also `DP in code` and [[Bob Jones|Bob]] linked.
- **Agenda:** Review the Data Platform Toolkit and the Data Platform roadmap. Meeting Notes - Test self-mention.
- **Decisions:** None recorded.
- **Action points:** Bob → ship the fix (v1.2); Alice Smith → other's task; Team → celebrate. Bob → second action
- **Notable:** fine.
- **Source:** `.sources/x.md`

### 2026-08-02 - Hyphen dash heading

- **Participants:** Bob
- **Agenda:** heading uses a plain hyphen
- **Decisions:** None recorded.
- **Action points:** Bob → action behind a hyphen heading
- **Notable:** thin
- **Source:** `.sources/y.md`
"""


def test_link(tmp):
    page = os.path.join(tmp, "wiki/meetings/Meeting Notes - Test.md")
    write(page, PAGE)
    r1 = run("register-link.py", "--pages", page, cwd=tmp)
    check("link: runs clean", r1.returncode == 0, r1.stderr)
    text = open(page, encoding="utf-8").read()
    fence = re.search(r"```markdown.*?```", text, re.S).group(0)
    check("link: fence untouched", "[[" not in fence, fence)
    check("link: fenced heading not an entry",
          "across 2 entries" in r1.stdout, r1.stdout)
    entry1 = text.split("### 2026-08-01")[1].split("### 2026-08-02")[0]
    check("link: pre-linked Bob not relinked within its entry",
          entry1.count("[[Bob Jones") == 1, str(entry1.count("[[Bob Jones")))
    check("link: inline code protected", "`DP in code`" in text)
    check("link: no self-link", "[[Meeting Notes - Test" not in text)
    check("link: longest alias wins",
          "[[Data Platform Toolkit]]" in text
          and "[[Data Platform (DP)|Data Platform]]" in text)
    r2 = run("register-link.py", "--pages", page, cwd=tmp)
    check("link: idempotent (second run adds 0)",
          r2.stdout.startswith("0 links"), r2.stdout)


def test_actions(tmp):
    cfg = os.path.join(tmp, ".vault-meta/register-actions.json")
    if os.path.exists(cfg):
        os.remove(cfg)
    r = run("register-actions.py", cwd=tmp)
    check("actions: unconfigured owner is a loud error",
          r.returncode != 0 and "no owner configured" in (r.stderr + r.stdout),
          r.stdout + r.stderr)
    write(cfg, json.dumps({"owner_page": "Bob Jones", "owner_names": ["Bob"]}))
    r = run("register-actions.py", cwd=tmp)
    check("actions: runs clean", r.returncode == 0, r.stderr)
    out = open(os.path.join(tmp, "wiki/meetings/Action Points.md"),
               encoding="utf-8").read()
    boxes = re.findall(r"(?m)^- \[.\] .*$", out)
    check("actions: 3 owner actions (incl. behind hyphen heading)",
          len(boxes) == 3, str(boxes))
    check("actions: other owners excluded",
          "other's task" not in out and "celebrate" not in out)
    check("actions: anchor verbatim for hyphen heading",
          "#2026-08-02 - Hyphen dash heading|source" in out, out[-400:])
    # tick one, rebuild, tick must survive
    out = out.replace("- [ ] **2026-08-02**", "- [x] **2026-08-02**", 1)
    write(os.path.join(tmp, "wiki/meetings/Action Points.md"), out)
    run("register-actions.py", cwd=tmp)
    out2 = open(os.path.join(tmp, "wiki/meetings/Action Points.md"),
                encoding="utf-8").read()
    check("actions: checked box survives rebuild",
          "- [x] **2026-08-02**" in out2)


def main():
    tmp = tempfile.mkdtemp(prefix="register-test-")
    try:
        make_vault(tmp)
        print("register-link.py")
        test_link(tmp)
        print("register-actions.py")
        test_actions(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    if FAILURES:
        print(f"\n{len(FAILURES)} FAILED: {FAILURES}")
        sys.exit(1)
    print("\nAll register-script tests passed.")


if __name__ == "__main__":
    main()
