#!/usr/bin/env python3
"""test_corpus_tier.py — hermetic tests for scripts/corpus-tier.py.

No network, no ollama, no index. A temp directory stands in for the vault, so
these never touch real config — which matters more than usual here: the file
under test owns a gitignored config file that cannot be recovered from git if
a test clobbers it.

Usage:
  python3 tests/test_corpus_tier.py
"""
import importlib.util
import json
import shutil
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HELPER = ROOT / "scripts" / "corpus-tier.py"

spec = importlib.util.spec_from_file_location("corpus_tier", HELPER)
ct = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ct)


class Fail(SystemExit):
    pass


def assert_eq(label, expected, actual):
    if expected != actual:
        raise Fail(f"FAIL {label}: expected {expected!r}, got {actual!r}")
    print(f"OK   {label}")


def assert_true(label, cond):
    if not cond:
        raise Fail(f"FAIL {label}")
    print(f"OK   {label}")


class Args:
    def __init__(self, **kw):
        self.folder = kw.get("folder")
        self.tier = kw.get("tier")
        self.bonus = kw.get("bonus")
        self.dry_run = kw.get("dry_run", False)
        self.json = kw.get("json", False)


def sandbox():
    """Point the module at a throwaway vault and return its root."""
    tmp = Path(tempfile.mkdtemp(prefix="corpus-tier-test-"))
    (tmp / ".sources").mkdir()
    (tmp / ".vault-meta").mkdir()
    ct.VAULT_ROOT = tmp
    ct.SRC = tmp / ".sources"
    ct.CONFIG = tmp / ".vault-meta" / "corpus-tiers.json"
    return tmp


# ─── Label inference must match corpus-index.py's tier_for() ────────────────
def test_auto_label():
    assert_eq("'Tier 1' infers '1'", "1", ct.auto_label("Tier 1"))
    assert_eq("'Who I AM (Tier 0)' infers '0'", "0",
              ct.auto_label("Who I AM (Tier 0)"))
    assert_eq("'Meeting Transcripts' slugifies", "meeting-transcripts",
              ct.auto_label("Meeting Transcripts"))


# ─── The bug that motivated this tool: an inline merge dropping siblings ────
def test_set_preserves_existing_entries():
    tmp = sandbox()
    try:
        ct.CONFIG.write_text(json.dumps({
            "map": {"DTM Forms": "forms", "Meeting Transcripts": "transcripts"},
            "bonus": {}}), encoding="utf-8")
        ct.cmd_set(Args(folder="Question Bank Archive", tier="archive",
                        bonus=-0.02))
        cfg = json.loads(ct.CONFIG.read_text(encoding="utf-8"))
        assert_eq("pre-existing mappings survive", "forms",
                  cfg["map"].get("DTM Forms"))
        assert_eq("second pre-existing mapping survives", "transcripts",
                  cfg["map"].get("Meeting Transcripts"))
        assert_eq("new mapping added", "archive",
                  cfg["map"].get("Question Bank Archive"))
        assert_eq("negative bonus recorded", -0.02,
                  cfg["bonus"].get("archive"))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_set_is_idempotent():
    tmp = sandbox()
    try:
        a = Args(folder="Archive", tier="archive", bonus=-0.02)
        ct.cmd_set(a)
        first = ct.CONFIG.read_text(encoding="utf-8")
        ct.cmd_set(Args(folder="Archive", tier="archive", bonus=-0.02))
        assert_eq("re-registering changes nothing", first,
                  ct.CONFIG.read_text(encoding="utf-8"))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_dry_run_writes_nothing():
    tmp = sandbox()
    try:
        ct.cmd_set(Args(folder="Archive", tier="archive", bonus=-0.02,
                        dry_run=True))
        assert_true("--dry-run leaves no config file", not ct.CONFIG.exists())
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ─── A bonus is keyed by LABEL, so folders can share it ─────────────────────
def test_remove_keeps_bonus_shared_with_another_folder():
    tmp = sandbox()
    try:
        ct.cmd_set(Args(folder="Old Summaries", tier="paraphrase",
                        bonus=-0.02))
        ct.cmd_set(Args(folder="New Summaries", tier="paraphrase"))
        ct.cmd_remove(Args(folder="Old Summaries"))
        cfg = json.loads(ct.CONFIG.read_text(encoding="utf-8"))
        assert_true("removed folder is unmapped",
                    "Old Summaries" not in cfg["map"])
        assert_eq("shared bonus retained for the remaining folder", -0.02,
                  cfg["bonus"].get("paraphrase"))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_remove_drops_bonus_when_last_user_goes():
    tmp = sandbox()
    try:
        ct.cmd_set(Args(folder="Only", tier="paraphrase", bonus=-0.02))
        ct.cmd_remove(Args(folder="Only"))
        cfg = json.loads(ct.CONFIG.read_text(encoding="utf-8"))
        assert_true("orphaned bonus removed",
                    "paraphrase" not in (cfg.get("bonus") or {}))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ─── Refuse to overwrite a file we cannot parse: it is gitignored, so a
#     clobber is unrecoverable ────────────────────────────────────────────────
def test_malformed_config_refuses_rather_than_overwrites():
    tmp = sandbox()
    try:
        ct.CONFIG.write_text("{not json", encoding="utf-8")
        try:
            ct.cmd_set(Args(folder="Archive", tier="archive", bonus=-0.02))
        except SystemExit:
            assert_eq("malformed config left untouched", "{not json",
                      ct.CONFIG.read_text(encoding="utf-8"))
            return
        raise Fail("FAIL malformed config did not abort")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ─── The point of the whole feature: the handicap is real and inspectable ───
def test_deprioritized_tier_ranks_below_tier_one():
    tmp = sandbox()
    try:
        (ct.SRC / "Tier 1").mkdir()
        (ct.SRC / "Summaries").mkdir()
        ct.cmd_set(Args(folder="Summaries", tier="paraphrase", bonus=-0.02))
        rows, merged = ct.effective(ct.read_config(),
                                    {"1": 0.030, "0": 0.020, "2": 0.015})
        by_folder = {f: b for f, _, b, _, _ in rows}
        assert_eq("Tier 1 keeps its default bonus", 0.030,
                  by_folder.get("Tier 1"))
        assert_eq("deprioritized tier is negative", -0.02,
                  by_folder.get("Summaries"))
        assert_true("handicap against tier 1 is 0.05",
                    abs((by_folder["Tier 1"] - by_folder["Summaries"])
                        - 0.05) < 1e-9)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_register_is_the_importable_seam():
    """A pipeline creating a .sources/ folder registers its own tier."""
    tmp = sandbox()
    try:
        ct.CONFIG.write_text(json.dumps({"map": {"DTM Forms": "forms"},
                                         "bonus": {}}), encoding="utf-8")
        changed = ct.register("Vendor Summaries", "vendor-summaries",
                              -0.02, note="why this tier exists")
        assert_true("first call reports changes", len(changed) == 3)
        cfg = json.loads(ct.CONFIG.read_text(encoding="utf-8"))
        assert_eq("sibling mapping untouched", "forms", cfg["map"]["DTM Forms"])
        assert_eq("note stored", "why this tier exists", cfg["_note"])
        assert_eq("second call is a no-op", [],
                  ct.register("Vendor Summaries", "vendor-summaries",
                              -0.02, note="why this tier exists"))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_register_rejects_empty_folder():
    tmp = sandbox()
    try:
        try:
            ct.register("   ")
        except ValueError:
            print("OK   empty folder raises ValueError")
            return
        raise Fail("FAIL empty folder was accepted")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_unmapped_folder_on_disk_is_reported():
    tmp = sandbox()
    try:
        (ct.SRC / "Tier 3").mkdir()
        rows, _ = ct.effective(ct.read_config(), {"1": 0.03})
        entry = [r for r in rows if r[0] == "Tier 3"]
        assert_eq("folder on disk appears once", 1, len(entry))
        assert_eq("its tier is inferred, not mapped", (False, True),
                  (entry[0][3], entry[0][4]))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    print("=== test_corpus_tier.py ===")
    test_auto_label()
    test_set_preserves_existing_entries()
    test_set_is_idempotent()
    test_dry_run_writes_nothing()
    test_remove_keeps_bonus_shared_with_another_folder()
    test_remove_drops_bonus_when_last_user_goes()
    test_malformed_config_refuses_rather_than_overwrites()
    test_deprioritized_tier_ranks_below_tier_one()
    test_register_is_the_importable_seam()
    test_register_rejects_empty_folder()
    test_unmapped_folder_on_disk_is_reported()
    print("\nAll corpus-tier tests passed.")


if __name__ == "__main__":
    main()
