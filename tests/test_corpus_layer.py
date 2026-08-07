#!/usr/bin/env python3
"""test_corpus_layer.py — hermetic tests for the corpus retrieval layer.

No network, no ollama, no LLM calls. Tests cover:
  - the REBIND CONTRACT: corpus-bm25.py / corpus-retrieve.py rebind
    bm25-index.py and rerank.py state constants by name; a rename in the
    stock scripts must fail here before it silently breaks the corpus tier
  - tier_for(): "Tier N" folders, slugified fallback, root files,
    corpus-tiers.json overrides
  - _tier_bonus(): defaults, config override, malformed-config tolerance
  - combined-retrieve query_tier(): ok / rebuilt+ok / unavailable mapping

Usage:
  python3 tests/test_corpus_layer.py
"""
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import types
import unittest.mock
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"

_passed = 0


def assert_true(name, cond, hint=""):
    global _passed
    if not cond:
        print(f"FAIL {name}" + (f"  [{hint}]" if hint else ""))
        sys.exit(1)
    _passed += 1
    print(f"OK   {name}")


def assert_eq(name, expected, actual):
    assert_true(name, expected == actual,
                hint=f"expected {expected!r}, got {actual!r}")


def import_script(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ─── The rebind contract ─────────────────────────────────────────────────────
def test_rebind_contract_bm25():
    """corpus-bm25.py rebinds these names in bm25-index.py. Renaming any of
    them upstream breaks the corpus tier — this test is the tripwire."""
    bm25 = import_script("bm25_contract", SCRIPTS / "bm25-index.py")
    for name in ("META_DIR", "CHUNKS_DIR", "BM25_DIR", "LOCK_PATH",
                 "EXCLUDE_CHUNK_IDS"):
        assert_true(f"bm25-index.py exposes {name}", hasattr(bm25, name))
    for fn in ("build_index", "write_index", "load_index", "query", "stats"):
        assert_true(f"bm25-index.py exposes {fn}()",
                    callable(getattr(bm25, fn, None)))


def test_rebind_contract_rerank():
    rerank = import_script("rerank_contract", SCRIPTS / "rerank.py")
    assert_true("rerank.py exposes VAULT_ROOT", hasattr(rerank, "VAULT_ROOT"))
    assert_true("rerank.py exposes rerank()",
                callable(getattr(rerank, "rerank", None)))


def test_rebind_contract_chunker():
    cp = import_script("cp_contract", SCRIPTS / "contextual-prefix.py")
    assert_true("contextual-prefix.py exposes chunk_body()",
                callable(getattr(cp, "chunk_body", None)))


def test_corpus_scripts_actually_rebind():
    """The corpus scripts' source must reference the rebound names — if the
    rebind block is ever deleted, the constants above stop being a contract."""
    src = (SCRIPTS / "corpus-bm25.py").read_text(encoding="utf-8")
    for name in ("META_DIR", "CHUNKS_DIR", "BM25_DIR", "LOCK_PATH"):
        assert_true(f"corpus-bm25.py rebinds {name}", name in src)
    src = (SCRIPTS / "corpus-retrieve.py").read_text(encoding="utf-8")
    assert_true("corpus-retrieve.py rebinds rerank VAULT_ROOT",
                "VAULT_ROOT" in src)


# ─── tier_for() ──────────────────────────────────────────────────────────────
def _fresh_corpus_index(overrides=None):
    ci = import_script("ci_test", SCRIPTS / "corpus-index.py")
    ci._TIER_OVERRIDES = overrides or {}
    return ci


def test_tier_for_tier_n_folders():
    ci = _fresh_corpus_index()
    assert_eq("'Tier 1/a.md' -> '1'", "1", ci.tier_for("Tier 1/a.md"))
    assert_eq("'Tier 3\\b.md' -> '3'", "3", ci.tier_for("Tier 3\\b.md"))
    assert_eq("'Who I AM (Tier 0)/x.md' -> '0'", "0",
              ci.tier_for("Who I AM (Tier 0)/x.md"))
    assert_eq("'My Tier 2 Docs/deep/c.md' -> '2'", "2",
              ci.tier_for("My Tier 2 Docs/deep/c.md"))


def test_tier_for_slug_fallback():
    ci = _fresh_corpus_index()
    assert_eq("'Meeting Transcripts/d.md' slugs", "meeting-transcripts",
              ci.tier_for("Meeting Transcripts/d.md"))
    assert_eq("'Reference  Material!/e.md' slugs", "reference-material",
              ci.tier_for("Reference  Material!/e.md"))


def test_tier_for_root_files_untired():
    ci = _fresh_corpus_index()
    assert_eq("root file has no tier", "", ci.tier_for("rootfile.md"))


def test_tier_for_override_wins():
    ci = _fresh_corpus_index({"meeting transcripts": "transcripts",
                              "tier 1": "gold"})
    assert_eq("override maps folder", "transcripts",
              ci.tier_for("Meeting Transcripts/d.md"))
    assert_eq("override beats Tier-N rule", "gold",
              ci.tier_for("Tier 1/a.md"))


def test_tier_overrides_reads_config():
    ci = import_script("ci_cfg", SCRIPTS / "corpus-index.py")
    with tempfile.TemporaryDirectory() as tmpdir:
        vault = Path(tmpdir)
        (vault / ".vault-meta").mkdir()
        (vault / ".vault-meta" / "corpus-tiers.json").write_text(
            json.dumps({"map": {"Special Folder": "s"}}), encoding="utf-8")
        ci.VAULT_ROOT = vault
        got = ci._tier_overrides()
        assert_eq("config map loaded (lowercased keys)", {"special folder": "s"}, got)
        (vault / ".vault-meta" / "corpus-tiers.json").write_text(
            "not json", encoding="utf-8")
        assert_eq("malformed config tolerated", {}, ci._tier_overrides())
        # Wrong-shape JSON must warn-and-default, never raise (found by
        # adversarial testing: a top-level array crashed the loader).
        for hostile in ('[1,2,3]', '{"map": 42}', '"just a string"'):
            (vault / ".vault-meta" / "corpus-tiers.json").write_text(
                hostile, encoding="utf-8")
            assert_eq(f"wrong-shape config tolerated: {hostile}", {},
                      ci._tier_overrides())


# ─── _tier_bonus() ───────────────────────────────────────────────────────────
def test_tier_bonus_defaults_and_override():
    cr = import_script("cr_test", SCRIPTS / "corpus-retrieve.py")
    with tempfile.TemporaryDirectory() as tmpdir:
        vault = Path(tmpdir)
        (vault / ".vault-meta").mkdir()
        cr.VAULT_ROOT = vault
        bonus = cr._tier_bonus()
        assert_eq("default bonus favours tier 1", 0.030, bonus["1"])
        assert_true("default bonus ranks 1 > 0 > 2",
                    bonus["1"] > bonus["0"] > bonus["2"])
        (vault / ".vault-meta" / "corpus-tiers.json").write_text(
            json.dumps({"bonus": {"gold": 0.05, "1": 0.001}}), encoding="utf-8")
        bonus = cr._tier_bonus()
        assert_eq("config adds new label", 0.05, bonus["gold"])
        assert_eq("config overrides default", 0.001, bonus["1"])
        for hostile in ('[1,2,3]', '{"bonus": [1]}',
                        '{"bonus": {"1": "not-a-number"}}'):
            (vault / ".vault-meta" / "corpus-tiers.json").write_text(
                hostile, encoding="utf-8")
            b = cr._tier_bonus()
            assert_eq(f"hostile bonus config keeps default: {hostile}",
                      0.030, b["1"])


# ─── combined-retrieve query_tier() status mapping ───────────────────────────
def _combined():
    return import_script("combined_test", SCRIPTS / "combined-retrieve.py")


def _proc(rc=0, stdout="", stderr=""):
    p = types.SimpleNamespace()
    p.returncode, p.stdout, p.stderr = rc, stdout, stderr
    return p


def test_query_tier_ok():
    cm = _combined()
    payload = json.dumps({"strategy": "bm25-only", "candidates": [{"x": 1}]})
    with unittest.mock.patch.object(cm, "run", return_value=_proc(0, payload)):
        t = cm.query_tier("wiki", "retrieve.py", "q", 5, False)
    assert_eq("clean run -> status ok", "ok", t["status"])
    assert_eq("candidates pass through", [{"x": 1}], t["candidates"])


def test_query_tier_rebuilt_ok():
    cm = _combined()
    payload = json.dumps({"strategy": "bm25-only", "candidates": []})
    err = "auto-build: wiki index stale — rebuilding (synthetic prefixes, no egress)"
    with unittest.mock.patch.object(cm, "run", return_value=_proc(0, payload, err)):
        t = cm.query_tier("wiki", "retrieve.py", "q", 5, False)
    assert_eq("auto-build note -> rebuilt+ok", "rebuilt+ok", t["status"])
    assert_true("note carries the auto-build line",
                t["note"].startswith("auto-build:"))


def test_query_tier_unavailable_on_rc():
    cm = _combined()
    with unittest.mock.patch.object(cm, "run",
                                    return_value=_proc(10, "", "ERR: nope")):
        t = cm.query_tier("corpus", "corpus-retrieve.py", "q", 5, False)
    assert_eq("nonzero rc -> unavailable", "unavailable", t["status"])
    assert_eq("no candidates", [], t["candidates"])


def test_query_tier_unavailable_on_garbage():
    cm = _combined()
    with unittest.mock.patch.object(cm, "run",
                                    return_value=_proc(0, "not json at all")):
        t = cm.query_tier("wiki", "retrieve.py", "q", 5, False)
    assert_eq("unparseable stdout -> unavailable", "unavailable", t["status"])


# ─── corpus tier end-to-end in a sandbox: build, corrupt, self-heal ──────────
def test_corpus_e2e_corrupt_index_self_heals():
    import shutil
    with tempfile.TemporaryDirectory() as tmpdir:
        sandbox = Path(tmpdir)
        (sandbox / "scripts").mkdir()
        (sandbox / ".vault-meta").mkdir()
        for f in ("corpus-retrieve.py", "corpus-index.py", "corpus-dedup.py",
                  "corpus-bm25.py", "bm25-index.py", "rerank.py",
                  "contextual-prefix.py", "tiling-check.py"):
            src = SCRIPTS / f
            if src.is_file():
                shutil.copy(src, sandbox / "scripts" / f)
        (sandbox / ".sources" / "Tier 1").mkdir(parents=True)
        (sandbox / ".sources" / "Tier 1" / "doc.md").write_text(
            "# Doc\n\nThe zoning variance applies to parcel nine.\n",
            encoding="utf-8")
        env = dict(os.environ, PYTHONUTF8="1")

        def q():
            return subprocess.run(
                [sys.executable, str(sandbox / "scripts" / "corpus-retrieve.py"),
                 "zoning variance parcel", "--top", "1", "--no-rerank"],
                capture_output=True, text=True, encoding="utf-8", env=env,
                timeout=120)

        r = q()
        assert_eq("first query auto-builds and answers", 0, r.returncode)
        assert_true("first query found the doc", "doc.md" in r.stdout,
                    hint=r.stdout[:200] + r.stderr[:200])

        idx = sandbox / ".vault-meta" / "corpus" / "bm25" / "index.json"
        idx.write_text("garbage{{{", encoding="utf-8")
        r2 = q()
        assert_eq("corrupt index self-heals to rc 0", 0, r2.returncode)
        assert_true("self-heal announced",
                    "unreadable" in r2.stderr, hint=r2.stderr[:300])
        assert_true("healed query still finds the doc", "doc.md" in r2.stdout)


def main():
    print("=== test_corpus_layer.py ===")
    test_rebind_contract_bm25()
    test_rebind_contract_rerank()
    test_rebind_contract_chunker()
    test_corpus_scripts_actually_rebind()
    test_tier_for_tier_n_folders()
    test_tier_for_slug_fallback()
    test_tier_for_root_files_untired()
    test_tier_for_override_wins()
    test_tier_overrides_reads_config()
    test_tier_bonus_defaults_and_override()
    test_query_tier_ok()
    test_query_tier_rebuilt_ok()
    test_query_tier_unavailable_on_rc()
    test_query_tier_unavailable_on_garbage()
    test_corpus_e2e_corrupt_index_self_heals()
    print(f"\nAll corpus-layer tests passed ({_passed} assertions).")


if __name__ == "__main__":
    main()
