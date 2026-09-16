#!/usr/bin/env python3
"""Hermetic tests for scripts/quickstart-state.py.

Every test runs against a sandboxed state path — the real vault's
.vault-meta/ is never touched.
"""

import importlib.util
import json
import shutil
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location(
    "quickstart_state", REPO / "scripts" / "quickstart-state.py")
qs = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(qs)

FAILURES = []


def assert_true(label, cond):
    if cond:
        print(f"OK   {label}")
    else:
        print(f"FAIL {label}")
        FAILURES.append(label)


def assert_eq(label, expected, actual):
    assert_true(f"{label} (expected {expected!r}, got {actual!r})"
                if expected != actual else label, expected == actual)


def sandbox():
    tmp = Path(tempfile.mkdtemp(prefix="qs-test-"))
    qs.STATE = tmp / "quickstart-state.json"
    return tmp


def run(*argv):
    """Invoke main() the way the CLI would; return exit code (None -> 0)."""
    try:
        rc = qs.main(["quickstart-state.py", *argv])
    except SystemExit as e:  # load() aborts via sys.exit
        rc = e.code
    return rc or 0


def test_lifecycle():
    tmp = sandbox()
    try:
        assert_eq("begin on empty succeeds", 0, run("begin"))
        assert_eq("answer records", 0, run("answer", "purpose", "research wiki"))
        assert_eq("skip records", 0, run("skip", "about"))
        assert_eq("step records", 0, run("step", "mode"))
        data = json.loads(qs.STATE.read_text(encoding="utf-8"))
        assert_eq("answer stored", "research wiki", data["answers"]["purpose"])
        assert_true("skip stored as None", data["answers"]["about"] is None)
        assert_eq("step stored", ["mode"], data["steps"])
        assert_eq("finish succeeds", 0, run("finish"))
        assert_true("done flag set",
                    json.loads(qs.STATE.read_text(encoding="utf-8"))["done"])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_begin_refuses_in_progress():
    tmp = sandbox()
    try:
        run("begin")
        run("answer", "purpose", "x")
        assert_eq("begin refuses IN_PROGRESS", 1, run("begin"))
        data = json.loads(qs.STATE.read_text(encoding="utf-8"))
        assert_eq("state survived refused begin", "x",
                  data["answers"]["purpose"])
        assert_eq("begin --force starts over", 0, run("begin", "--force"))
        data = json.loads(qs.STATE.read_text(encoding="utf-8"))
        assert_eq("forced begin is fresh", {}, data["answers"])
        run("finish")
        assert_eq("begin after DONE needs no force", 0, run("begin"))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_validation():
    tmp = sandbox()
    try:
        assert_eq("answer before begin fails", 1, run("answer", "purpose", "x"))
        run("begin")
        assert_eq("unknown KEY rejected", 1, run("answer", "colour", "blue"))
        assert_eq("empty VALUE rejected", 1, run("answer", "purpose", " "))
        assert_eq("unknown step rejected", 1, run("step", "deploy"))
        assert_eq("step is idempotent", 0, run("step", "mode"))
        assert_eq("step twice still one entry", 0, run("step", "mode"))
        data = json.loads(qs.STATE.read_text(encoding="utf-8"))
        assert_eq("no duplicate steps", ["mode"], data["steps"])
        assert_eq("multi-word answers join", 0,
                  run("answer", "purpose", "a", "b", "c"))
        data = json.loads(qs.STATE.read_text(encoding="utf-8"))
        assert_eq("joined value", "a b c", data["answers"]["purpose"])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_malformed_aborts_but_clear_works():
    tmp = sandbox()
    try:
        qs.STATE.parent.mkdir(parents=True, exist_ok=True)
        qs.STATE.write_text("{not json", encoding="utf-8")
        assert_eq("status aborts on malformed", 1, run("status"))
        assert_eq("begin aborts on malformed", 1, run("begin"))
        assert_true("malformed file NOT overwritten",
                    qs.STATE.read_text(encoding="utf-8") == "{not json")
        assert_eq("clear works on malformed", 0, run("clear"))
        assert_true("file gone after clear", not qs.STATE.exists())
        qs.STATE.write_text(json.dumps({"foo": 1}), encoding="utf-8")
        assert_eq("wrong-shape JSON aborts", 1, run("status"))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_status_output():
    tmp = sandbox()
    try:
        # status on NOT_STARTED must not create the file
        run("status")
        assert_true("status does not create state", not qs.STATE.exists())
        run("begin")
        run("skip", "purpose")
        run("step", "mode")
        data = json.loads(qs.STATE.read_text(encoding="utf-8"))
        assert_eq("version stamped", 1, data["version"])
        assert_true("no tmp file left behind",
                    not list(qs.STATE.parent.glob("*.tmp")))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    test_lifecycle()
    test_begin_refuses_in_progress()
    test_validation()
    test_malformed_aborts_but_clear_works()
    test_status_output()
    if FAILURES:
        print(f"\n{len(FAILURES)} FAILURE(S)")
        sys.exit(1)
    print("\nAll quickstart-state tests passed.")
