#!/usr/bin/env python3
"""test_lineage_check.py — hermetic tests for scripts/lineage-check.py.

Covers: frontmatter parsing (scalar, quoted, inline list, block list, no
fence), wikilink target extraction, dangling/asymmetry/cycle detection,
chain construction with heads first, forks, CLI exit codes and JSON output.
No network, no LLM.

Usage:
  python3 tests/test_lineage_check.py
"""
import importlib.util
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HELPER = ROOT / "scripts" / "lineage-check.py"

spec = importlib.util.spec_from_file_location("lineage_check", HELPER)
lc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lc)


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


def test_parse_frontmatter():
    fm = lc.parse_frontmatter('---\ntitle: Test\nsupersedes: "[[Old Page]]"\n---\nbody')
    assert_eq("scalar quoted", '"[[Old Page]]"', fm["supersedes"])
    fm = lc.parse_frontmatter("---\nsupersedes:\n  - \"[[A]]\"\n  - \"[[B]]\"\n---\n")
    assert_eq("block list", ['"[[A]]"', '"[[B]]"'], fm["supersedes"])
    assert_eq("no fence", {}, lc.parse_frontmatter("# Just a heading\n"))


def test_extract_targets():
    assert_eq("plain wikilink", ["Old Page"], lc.extract_targets("[[Old Page]]"))
    assert_eq("quoted wikilink", ["Old Page"], lc.extract_targets('"[[Old Page]]"'))
    assert_eq("inline list", ["A", "B"],
              lc.extract_targets('["[[A]]", "[[B]]"]'))
    assert_eq("block list (already list)", ["A", "B"],
              lc.extract_targets(['"[[A]]"', '"[[B]]"']))
    assert_eq("alias link", ["Real Page"],
              lc.extract_targets("[[Real Page|shown text]]"))
    assert_eq("bare name tolerated", ["Old Page"], lc.extract_targets("Old Page"))


def write_page(root, name, **fields):
    lines = ["---", f"title: {name}"]
    for k, v in fields.items():
        lines.append(f"{k}: \"[[{v}]]\"" if isinstance(v, str) else k + ":")
        if isinstance(v, list):
            lines.extend(f'  - "[[{item}]]"' for item in v)
    lines += ["---", "", f"# {name}"]
    (root / f"{name}.md").write_text("\n".join(lines), encoding="utf-8")


def build_vault(tmp):
    root = Path(tmp) / "wiki"
    root.mkdir()
    # Clean chain: Rev2 → Rev1 → 2017 (symmetric).
    write_page(root, "Policy Rev2", supersedes="Policy Rev1")
    write_page(root, "Policy Rev1", supersedes="Policy 2017",
               superseded_by="Policy Rev2")
    write_page(root, "Policy 2017", superseded_by="Policy Rev1")
    # Dangling: points at a page that doesn't exist.
    write_page(root, "Framework 2nd Ed", supersedes="Framework 1st Ed")
    # Asymmetric pair: A claims to supersede B; B silent.
    write_page(root, "Template v2", supersedes="Template v1")
    write_page(root, "Template v1")
    # Unrelated page with no lineage.
    write_page(root, "Standalone")
    return root


def test_check_findings():
    with tempfile.TemporaryDirectory() as tmp:
        root = build_vault(tmp)
        findings = lc.check(lc.scan(root))
        kinds = sorted(f["kind"] for f in findings)
        assert_eq("finding kinds", ["asymmetry", "dangling"], kinds)
        dangling = next(f for f in findings if f["kind"] == "dangling")
        assert_eq("dangling target", "Framework 1st Ed", dangling["target"])
        asym = next(f for f in findings if f["kind"] == "asymmetry")
        assert_eq("asymmetry page", "Template v2", asym["page"])
        assert_true("asymmetry fix names file",
                    "Template v1.md" in asym["fix"], asym["fix"])


def test_symmetric_chain_clean():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "wiki"
        root.mkdir()
        write_page(root, "B", supersedes="A")
        write_page(root, "A", superseded_by="B")
        assert_eq("symmetric chain has no findings", [],
                  lc.check(lc.scan(root)))


def test_cycle():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "wiki"
        root.mkdir()
        write_page(root, "X", supersedes="Y", superseded_by="Y")
        write_page(root, "Y", supersedes="X", superseded_by="X")
        findings = lc.check(lc.scan(root))
        assert_true("cycle detected",
                    any(f["kind"] == "cycle" for f in findings),
                    str(findings))


def test_chains():
    with tempfile.TemporaryDirectory() as tmp:
        root = build_vault(tmp)
        result = lc.chains(lc.scan(root))
        chain = next(c for c in result if "Policy Rev2" in c)
        assert_eq("chain head first",
                  ["Policy Rev2", "Policy Rev1", "Policy 2017"], chain)
        assert_true("standalone not in chains",
                    all("Standalone" not in c for c in result))
        # Fork: two successors of one ancestor → two heads, ancestor in both.
        write_page(root, "Fork A", supersedes="Common")
        write_page(root, "Fork B", supersedes="Common")
        write_page(root, "Common",
                   superseded_by=["Fork A", "Fork B"])
        forked = lc.chains(lc.scan(root))
        heads = [c[0] for c in forked]
        assert_true("both fork heads present",
                    "Fork A" in heads and "Fork B" in heads, str(heads))


def test_cli():
    with tempfile.TemporaryDirectory() as tmp:
        root = build_vault(tmp)
        r = subprocess.run(
            [sys.executable, str(HELPER), "--root", str(root), "check", "--json"],
            capture_output=True, text=True, encoding="utf-8")
        assert_eq("check exit 1 with findings", 1, r.returncode)
        data = json.loads(r.stdout)
        assert_eq("json findings count", 2, len(data))

        clean = Path(tmp) / "clean"
        clean.mkdir()
        write_page(clean, "Solo")
        r2 = subprocess.run(
            [sys.executable, str(HELPER), "--root", str(clean), "check"],
            capture_output=True, text=True, encoding="utf-8")
        assert_eq("check exit 0 when clean", 0, r2.returncode)

        r3 = subprocess.run(
            [sys.executable, str(HELPER), "--root", str(root), "chains"],
            capture_output=True, text=True, encoding="utf-8")
        assert_eq("chains exit 0", 0, r3.returncode)
        assert_true("chains arrow output",
                    "Policy Rev2  →  Policy Rev1  →  Policy 2017" in r3.stdout,
                    r3.stdout)

        r4 = subprocess.run(
            [sys.executable, str(HELPER), "--root", str(Path(tmp) / "missing"),
             "check"],
            capture_output=True, text=True, encoding="utf-8")
        assert_eq("missing root exit 2", 2, r4.returncode)


def main():
    test_parse_frontmatter()
    test_extract_targets()
    test_check_findings()
    test_symmetric_chain_clean()
    test_cycle()
    test_chains()
    test_cli()
    print("\ntest_lineage_check.py: all tests passed.")


if __name__ == "__main__":
    main()
