#!/usr/bin/env python3
"""test_ingest_depth.py — hermetic tests for scripts/ingest-depth.py.

Covers: heuristic proposals (junk/structured/deck/large/document/default),
plan round-trip via CLI, override preservation across re-assign, get default
for unplanned files, set validation, dry-run non-persistence, summary counts.
No network, no LLM.

Usage:
  python3 tests/test_ingest_depth.py
"""
import importlib.util
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HELPER = ROOT / "scripts" / "ingest-depth.py"

spec = importlib.util.spec_from_file_location("ingest_depth", HELPER)
idp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(idp)


class Fail(SystemExit):
    pass


def assert_eq(label, expected, actual):
    if expected != actual:
        raise Fail(f"FAIL {label}: expected {expected!r}, got {actual!r}")
    print(f"OK   {label}")


def assert_true(label, cond, hint=""):
    if not cond:
        raise Fail(f"FAIL {label}{(': ' + hint) if hint else ''}")
    print(f"OK   {label}")


def test_propose_depth():
    cases = [
        ("desktop.ini", 100, "skip", "os-junk"),
        ("Thumbs.db", 100, "skip", "os-junk"),
        ("survey.xlsx", 100, "catalogue", "structured-data"),
        ("codes.csv", 10_000_000, "catalogue", "structured-data"),
        ("training-deck.pptx", 100, "summary", "slide-deck"),
        ("handbook.pdf", 5_000_000, "summary", None),
        ("memo.pdf", 100_000, "full", "document"),
        ("notes.md", 500, "full", "document"),
        ("big-notes.md", 3_000_000, "summary", None),
        ("photo.png", 100, "full", "default"),
        ("archive.zip", 100, "full", "default"),
    ]
    for name, size, want_depth, want_reason in cases:
        depth, reason = idp.propose_depth(name, size)
        assert_eq(f"propose {name}", want_depth, depth)
        if want_reason:
            assert_eq(f"reason {name}", want_reason, reason)


def test_threshold_param():
    depth, _ = idp.propose_depth("doc.pdf", 150_000, large_bytes=100_000)
    assert_eq("custom threshold demotes", "summary", depth)
    depth, _ = idp.propose_depth("doc.pdf", 150_000, large_bytes=200_000)
    assert_eq("custom threshold keeps full", "full", depth)


def run_cli(plan, *argv):
    return subprocess.run(
        [sys.executable, str(HELPER), "--plan", str(plan), *argv],
        capture_output=True, text=True,
    )


def test_cli_roundtrip():
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        plan = tmp / "plan.json"
        corpus = tmp / "corpus"
        corpus.mkdir()
        (corpus / "memo.md").write_text("hello", encoding="utf-8")
        (corpus / "data.xlsx").write_bytes(b"x" * 10)
        (corpus / "deck.pptx").write_bytes(b"x" * 10)
        (corpus / "desktop.ini").write_text("junk", encoding="utf-8")

        r = run_cli(plan, "assign", str(corpus))
        assert_eq("assign exit 0", 0, r.returncode)
        data = json.loads(plan.read_text(encoding="utf-8"))
        assert_eq("assign planned 4", 4, len(data["entries"]))

        r = run_cli(plan, "get", str(corpus / "data.xlsx"))
        assert_eq("get catalogue", "catalogue", r.stdout.strip())
        r = run_cli(plan, "get", str(corpus / "unplanned-file.md"))
        assert_eq("get unplanned defaults to full", "full", r.stdout.strip())

        # Operator override survives re-assign.
        r = run_cli(plan, "set", str(corpus / "data.xlsx"), "full",
                    "--reason", "this one is actually prose")
        assert_eq("set exit 0", 0, r.returncode)
        r = run_cli(plan, "assign", str(corpus))
        assert_true("re-assign reports preserved override",
                    "overrides-preserved: 1" in r.stderr, r.stderr)
        r = run_cli(plan, "get", str(corpus / "data.xlsx"))
        assert_eq("override survived re-assign", "full", r.stdout.strip())

        r = run_cli(plan, "set", str(corpus / "memo.md"), "bogus-depth")
        assert_eq("invalid depth rejected", 2, r.returncode)

        r = run_cli(plan, "summary")
        counts = json.loads(r.stdout)
        assert_eq("summary total", 4, counts["total"])
        assert_eq("summary overrides", 1, counts["overrides"])
        assert_eq("summary skip", 1, counts["skip"])

        r = run_cli(plan, "list", "--depth", "summary")
        listed = json.loads(r.stdout)
        assert_eq("list filters by depth", 1, len(listed))
        assert_true("list shows deck", any("deck.pptx" in k for k in listed))


def test_dry_run():
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        plan = tmp / "plan.json"
        f = tmp / "a.md"
        f.write_text("x", encoding="utf-8")
        r = run_cli(plan, "assign", str(f), "--dry-run")
        assert_eq("dry-run exit 0", 0, r.returncode)
        assert_true("dry-run prints plan", '"entries"' in r.stdout)
        assert_true("dry-run does not write", not plan.exists())


def test_corrupt_plan():
    with tempfile.TemporaryDirectory() as tmp:
        plan = Path(tmp) / "plan.json"
        plan.write_text("{not json", encoding="utf-8")
        r = run_cli(plan, "summary")
        assert_true("corrupt plan fails loudly", r.returncode != 0)
        assert_true("corrupt plan names file", "corrupt plan" in r.stderr)


def main():
    test_propose_depth()
    test_threshold_param()
    test_cli_roundtrip()
    test_dry_run()
    test_corrupt_plan()
    print("\ntest_ingest_depth.py: all tests passed.")


if __name__ == "__main__":
    main()
