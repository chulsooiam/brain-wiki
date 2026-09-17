#!/usr/bin/env python3
"""Project-scope commands must mirror their plugin-scope originals.

commands/<name>.md is what the plugin install exposes; .claude/commands/<name>.md
is what a cloned vault exposes with no plugin. They are the same command and
must say the same thing. The mirror may differ only by (a) the leading
provenance note and (b) skill references expanded to their file path.
"""

import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
MIRRORED = ("wiki", "quickstart", "save", "canvas", "autoresearch")
FAILURES = []


def check(label, cond):
    print(("OK   " if cond else "FAIL ") + label)
    if not cond:
        FAILURES.append(label)


def normalise(text):
    text = re.sub(r"^> Project-scope copy of .*?\n\n", "", text, count=1,
                  flags=re.S | re.M)
    text = re.sub(r"skill \(`skills/[\w-]+/SKILL\.md` at the vault root\)",
                  "skill", text)
    return text.strip()


def main():
    for name in MIRRORED:
        root = REPO / "commands" / f"{name}.md"
        mirror = REPO / ".claude" / "commands" / f"{name}.md"
        check(f"{name}: mirror exists", mirror.exists())
        if not mirror.exists():
            continue
        a = normalise(root.read_text(encoding="utf-8"))
        b = normalise(mirror.read_text(encoding="utf-8"))
        check(f"{name}: mirror matches root command", a == b)
        check(f"{name}: mirror carries provenance note",
              "Project-scope copy of" in mirror.read_text(encoding="utf-8"))
    if FAILURES:
        print(f"\n{len(FAILURES)} FAILURE(S) — regenerate the mirror from the "
              "root command (see CONTRIBUTING).")
        sys.exit(1)
    print("\nAll command-parity tests passed.")


if __name__ == "__main__":
    main()
