#!/usr/bin/env python3
"""quickstart-state.py — durable progress state for the /quickstart interview.

The quickstart skill records every answer and every completed build step here,
so an interrupted run resumes from where it stopped instead of re-asking the
user. The state file is runtime-only and gitignored; the durable record of the
interview is wiki/meta/quickstart-brief.md, written by the skill at the end.

Usage:
  quickstart-state.py status                    # NOT_STARTED | IN_PROGRESS | DONE (+ detail)
  quickstart-state.py begin                     # create fresh state (refuses to clobber IN_PROGRESS)
  quickstart-state.py begin --force             # start over regardless
  quickstart-state.py answer KEY VALUE          # record an interview answer
  quickstart-state.py skip KEY                  # record an explicit skip
  quickstart-state.py step NAME                 # mark a build step completed
  quickstart-state.py finish                    # mark the whole quickstart DONE
  quickstart-state.py clear                     # delete the state file

KEY is one of: purpose, about, mode, sources, pages.
NAME is one of: mode, scaffold, sources, pages, ingest, report.

A malformed state file ABORTS every command except clear: silently replacing
it would erase the record of what has already been built into the vault.
"""

import json
import os
import sys
from pathlib import Path

VAULT_ROOT = Path(__file__).resolve().parent.parent
STATE = VAULT_ROOT / ".vault-meta" / "quickstart-state.json"

QUESTION_KEYS = ("purpose", "about", "mode", "sources", "pages")
BUILD_STEPS = ("mode", "scaffold", "sources", "pages", "ingest", "report")


def load():
    """Return parsed state, None if absent. Malformed file aborts loudly."""
    if not STATE.exists():
        return None
    try:
        data = json.loads(STATE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        print(f"ERROR: {STATE} is unreadable ({e}).", file=sys.stderr)
        print("Refusing to overwrite a record of work already done.",
              file=sys.stderr)
        print("Inspect it, then `quickstart-state.py clear` if it is beyond "
              "repair.", file=sys.stderr)
        sys.exit(1)
    if not isinstance(data, dict) or "answers" not in data or "steps" not in data:
        print(f"ERROR: {STATE} does not look like quickstart state.",
              file=sys.stderr)
        sys.exit(1)
    return data


def save(data):
    """Atomic write: tmp in the same directory, then rename."""
    STATE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n",
                   encoding="utf-8")
    os.replace(tmp, STATE)


def fresh():
    return {"version": 1, "done": False, "answers": {}, "steps": []}


def cmd_status(state):
    if state is None:
        print("NOT_STARTED")
        return
    if state.get("done"):
        print("DONE")
        return
    print("IN_PROGRESS")
    answered = state["answers"]
    for k in QUESTION_KEYS:
        if k in answered:
            v = answered[k]
            print(f"  {k}: {'(skipped)' if v is None else 'answered'}")
        else:
            print(f"  {k}: pending")
    done_steps = state["steps"]
    for s in BUILD_STEPS:
        print(f"  build/{s}: {'done' if s in done_steps else 'pending'}")


def main(argv):
    if len(argv) < 2:
        print(__doc__.strip(), file=sys.stderr)
        return 2
    cmd, args = argv[1], argv[2:]

    if cmd == "clear":
        if STATE.exists():
            STATE.unlink()
            print("cleared")
        else:
            print("nothing to clear")
        return 0

    state = load()

    if cmd == "status":
        cmd_status(state)
        return 0

    if cmd == "begin":
        if state is not None and not state.get("done") and "--force" not in args:
            print("ERROR: a quickstart is already IN_PROGRESS. Resume it, or "
                  "`begin --force` to start over.", file=sys.stderr)
            return 1
        save(fresh())
        print("begun")
        return 0

    if state is None:
        print("ERROR: no quickstart in progress. Run `begin` first.",
              file=sys.stderr)
        return 1

    if cmd in ("answer", "skip"):
        if not args or args[0] not in QUESTION_KEYS:
            print(f"ERROR: KEY must be one of {', '.join(QUESTION_KEYS)}.",
                  file=sys.stderr)
            return 1
        key = args[0]
        if cmd == "answer":
            if len(args) < 2 or not args[1].strip():
                print("ERROR: answer requires a non-empty VALUE (use `skip` "
                      "to record a skip).", file=sys.stderr)
                return 1
            state["answers"][key] = " ".join(args[1:])
        else:
            state["answers"][key] = None
        save(state)
        print(f"recorded {key}")
        return 0

    if cmd == "step":
        if not args or args[0] not in BUILD_STEPS:
            print(f"ERROR: NAME must be one of {', '.join(BUILD_STEPS)}.",
                  file=sys.stderr)
            return 1
        if args[0] not in state["steps"]:
            state["steps"].append(args[0])
            save(state)
        print(f"step {args[0]} done")
        return 0

    if cmd == "finish":
        state["done"] = True
        save(state)
        print("quickstart DONE")
        return 0

    print(f"ERROR: unknown command {cmd!r}.", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
