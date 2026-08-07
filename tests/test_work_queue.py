#!/usr/bin/env python3
"""test_work_queue.py — hermetic tests for scripts/work-queue.py.

Covers: entry-line regex round-trip, add/list/done/stats CLI flow, add
idempotency per (kind, page) with detail refresh, done with and without
kind filter, exit 1 on unmatched done, kind validation, checked-off-in-
Obsidian entries surviving a reload, empty-queue rendering. No network.

Usage:
  python3 tests/test_work_queue.py
"""
import importlib.util
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HELPER = ROOT / "scripts" / "work-queue.py"

spec = importlib.util.spec_from_file_location("work_queue", HELPER)
wq = importlib.util.module_from_spec(spec)
spec.loader.exec_module(wq)


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


def run_cli(queue, *argv):
    return subprocess.run(
        [sys.executable, str(HELPER), "--queue", str(queue), *argv],
        capture_output=True, text=True, encoding="utf-8")


def test_roundtrip():
    entries = [
        {"kind": "stale", "page": "Policy Rev1", "detail": "outdated by Rev2",
         "added": "2026-08-01", "done": False, "done_date": None},
        {"kind": "stub-expand", "page": "GDI", "detail": "3 inbound links",
         "added": "2026-08-02", "done": True, "done_date": "2026-08-07"},
    ]
    with tempfile.TemporaryDirectory() as tmp:
        q = Path(tmp) / "queue.md"
        wq.save(entries, q)
        loaded = wq.load(q)
        assert_eq("roundtrip count", 2, len(loaded))
        assert_eq("roundtrip open entry", entries[0], loaded[1] if loaded[1]["kind"] == "stale" else loaded[0])
        done = next(e for e in loaded if e["done"])
        assert_eq("roundtrip done date", "2026-08-07", done["done_date"])
        text = q.read_text(encoding="utf-8")
        assert_true("frontmatter present", text.startswith("---"))
        assert_true("open section", "## Open" in text)
        assert_true("done section", "## Done" in text)


def test_cli_flow():
    with tempfile.TemporaryDirectory() as tmp:
        q = Path(tmp) / "queue.md"

        r = run_cli(q, "add", "--kind", "stale", "--page", "Policy Rev1",
                    "--detail", "claim outdated", "--date", "2026-08-07")
        assert_eq("add exit 0", 0, r.returncode)

        # Idempotent add: same kind+page updates detail, no duplicate.
        r = run_cli(q, "add", "--kind", "stale", "--page", "Policy Rev1",
                    "--detail", "claim outdated by Rev2", "--date", "2026-08-07")
        assert_true("second add updates", "updated" in r.stdout, r.stdout)
        r = run_cli(q, "list", "--json")
        items = json.loads(r.stdout)
        assert_eq("no duplicate", 1, len(items))
        assert_eq("detail refreshed", "claim outdated by Rev2",
                  items[0]["detail"])

        run_cli(q, "add", "--kind", "lineage", "--page", "Policy Rev1",
                "--detail", "missing superseded_by", "--date", "2026-08-07")
        run_cli(q, "add", "--kind", "stale", "--page", "Other Page",
                "--detail", "x", "--date", "2026-08-07")

        r = run_cli(q, "list", "--kind", "stale", "--json")
        assert_eq("kind filter", 2, len(json.loads(r.stdout)))

        # done with kind filter: only the lineage entry closes.
        r = run_cli(q, "done", "--page", "Policy Rev1", "--kind", "lineage",
                    "--date", "2026-08-08")
        assert_eq("done exit 0", 0, r.returncode)
        r = run_cli(q, "list", "--json")
        assert_eq("one closed, two open", 2, len(json.loads(r.stdout)))

        # done without kind closes remaining entries for that page.
        r = run_cli(q, "done", "--page", "Policy Rev1", "--date", "2026-08-08")
        assert_eq("done all-kinds exit 0", 0, r.returncode)

        r = run_cli(q, "done", "--page", "Nonexistent")
        assert_eq("unmatched done exit 1", 1, r.returncode)

        r = run_cli(q, "stats")
        stats = json.loads(r.stdout)
        assert_eq("stats open", 1, stats["open"])
        assert_eq("stats done", 2, stats["done"])
        assert_eq("stats by_kind", {"stale": 1}, stats["by_kind"])

        r = run_cli(q, "add", "--kind", "Not A Token!", "--page", "P",
                    "--detail", "d")
        assert_eq("bad kind exit 2", 2, r.returncode)


def test_obsidian_checkoff_survives():
    with tempfile.TemporaryDirectory() as tmp:
        q = Path(tmp) / "queue.md"
        run_cli(q, "add", "--kind", "manual", "--page", "Some Page",
                "--detail", "check me off", "--date", "2026-08-07")
        # Simulate the user ticking the box in Obsidian (no done date).
        q.write_text(q.read_text(encoding="utf-8").replace(
            "- [ ] `manual`", "- [x] `manual`"), encoding="utf-8")
        loaded = wq.load(q)
        assert_true("checked-off entry parsed as done", loaded[0]["done"])
        r = run_cli(q, "stats")
        assert_eq("checked-off counted done", 1, json.loads(r.stdout)["done"])


def test_empty_render():
    with tempfile.TemporaryDirectory() as tmp:
        q = Path(tmp) / "queue.md"
        wq.save([], q)
        assert_true("empty queue placeholder",
                    "_Queue is empty._" in q.read_text(encoding="utf-8"))
        assert_eq("empty queue loads clean", [], wq.load(q))


def main():
    test_roundtrip()
    test_cli_flow()
    test_obsidian_checkoff_survives()
    test_empty_render()
    print("\ntest_work_queue.py: all tests passed.")


if __name__ == "__main__":
    main()
