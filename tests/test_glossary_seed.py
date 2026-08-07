#!/usr/bin/env python3
"""test_glossary_seed.py — hermetic tests for scripts/glossary-seed.py.

Covers: column auto-detection and overrides (header + index), csv/xlsx input
(xlsx via the same synthetic fixture technique as test_form_catalogue),
stub content and filename sanitization, idempotency (existing stubs never
overwritten without --force), dedup of repeated terms, glossary index
generation, dry-run non-persistence, usage errors. No network, no LLM.

Usage:
  python3 tests/test_glossary_seed.py
"""
import importlib.util
import json
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HELPER = ROOT / "scripts" / "glossary-seed.py"

spec = importlib.util.spec_from_file_location("glossary_seed", HELPER)
gs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gs)


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


CSV = """Acronym,Definition,Notes
DTM,Displacement Tracking Matrix,core
AAP,Accountability to Affected Populations,
DTM,duplicate row should be ignored,
A/B:Test,Term with bad filename chars,
NoDef,,
"""


def run_cli(*argv):
    return subprocess.run([sys.executable, str(HELPER), *argv],
                          capture_output=True, text=True, encoding="utf-8")


def test_safe_filename():
    assert_eq("bad chars replaced", "A-B-Test", gs.safe_filename('A/B:Test'))
    assert_eq("plain name kept", "DTM", gs.safe_filename("DTM"))
    assert_eq("empty becomes placeholder", "unnamed-term", gs.safe_filename("///"))


def test_csv_seed():
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        table = tmp / "acronyms.csv"
        table.write_text(CSV, encoding="utf-8")
        out = tmp / "entities"

        r = run_cli(str(table), "--out", str(out), "--date", "2026-08-07")
        assert_eq("seed exit 0", 0, r.returncode)
        summary = json.loads(r.stdout)
        assert_eq("created count (dedup applied)", 4, summary["created"])
        assert_eq("terms total", 4, summary["terms_total"])

        stub = (out / "DTM.md").read_text(encoding="utf-8")
        assert_true("stub frontmatter", "glossary_seed: true" in stub)
        assert_true("stub date", "created: 2026-08-07" in stub)
        assert_true("stub definition", "Displacement Tracking Matrix" in stub)
        assert_true("stub dedup kept first definition",
                    "duplicate row" not in stub)
        assert_true("sanitized filename exists", (out / "A-B-Test.md").exists())
        nodef = (out / "NoDef.md").read_text(encoding="utf-8")
        assert_true("empty definition placeholder",
                    "_No definition provided" in nodef)

        idx = (out / "glossary.md").read_text(encoding="utf-8")
        assert_true("index links sanitized name with alias",
                    "[[A-B-Test|A/B:Test]]" in idx)
        assert_true("index alphabetical",
                    idx.index("[[AAP") < idx.index("[[DTM"), )

        # Idempotency: edit a stub, re-run, edit must survive.
        (out / "DTM.md").write_text(stub + "\nEXPANDED CONTENT\n",
                                    encoding="utf-8")
        r2 = run_cli(str(table), "--out", str(out))
        summary2 = json.loads(r2.stdout)
        assert_eq("re-run creates nothing", 0, summary2["created"])
        assert_eq("re-run skips existing", 4, summary2["skipped_existing"])
        assert_true("expanded stub untouched",
                    "EXPANDED CONTENT" in
                    (out / "DTM.md").read_text(encoding="utf-8"))


def test_column_overrides():
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        table = tmp / "weird.csv"
        table.write_text("Sigla,Significado\nOIM,Organización\n", encoding="utf-8")
        out = tmp / "e"

        r = run_cli(str(table), "--out", str(out))
        assert_eq("undetectable columns exit 2", 2, r.returncode)

        r2 = run_cli(str(table), "--out", str(out),
                     "--term-col", "Sigla", "--def-col", "1")
        assert_eq("overrides work (header + index)", 0, r2.returncode)
        assert_true("seeded via override", (out / "OIM.md").exists())


def test_xlsx_input():
    M = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    PR = "http://schemas.openxmlformats.org/package/2006/relationships"

    def cell(ref, val):
        return f'<c r="{ref}" t="inlineStr"><is><t>{val}</t></is></c>'

    sheet = (f'<worksheet xmlns="{M}"><sheetData>'
             f'<row r="1">{cell("A1", "Term")}{cell("B1", "Expansion")}</row>'
             f'<row r="2">{cell("A2", "GDI")}{cell("B2", "Global Data Institute")}</row>'
             f'</sheetData></worksheet>')
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        f = tmp / "terms.xlsx"
        with zipfile.ZipFile(f, "w") as z:
            z.writestr("xl/workbook.xml",
                       f'<workbook xmlns="{M}" xmlns:r="{R}"><sheets>'
                       f'<sheet name="Sheet1" sheetId="1" r:id="rId1"/>'
                       f'</sheets></workbook>')
            z.writestr("xl/_rels/workbook.xml.rels",
                       f'<Relationships xmlns="{PR}"><Relationship Id="rId1" '
                       f'Type="{R}/worksheet" Target="worksheets/sheet1.xml"/>'
                       f'</Relationships>')
            z.writestr("xl/worksheets/sheet1.xml", sheet)
        out = tmp / "e"
        r = run_cli(str(f), "--out", str(out))
        assert_eq("xlsx seed exit 0", 0, r.returncode)
        assert_true("xlsx stub created", (out / "GDI.md").exists())
        assert_true("xlsx definition present",
                    "Global Data Institute" in
                    (out / "GDI.md").read_text(encoding="utf-8"))


def test_dry_run_and_errors():
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        table = tmp / "a.csv"
        table.write_text("term,definition\nX,Y\n", encoding="utf-8")
        out = tmp / "e"
        r = run_cli(str(table), "--out", str(out), "--dry-run")
        assert_eq("dry-run exit 0", 0, r.returncode)
        assert_true("dry-run writes nothing", not out.exists())

        r2 = run_cli(str(tmp / "missing.csv"), "--out", str(out))
        assert_eq("missing file exit 2", 2, r2.returncode)

        bad = tmp / "notes.txt"
        bad.write_text("x", encoding="utf-8")
        r3 = run_cli(str(bad), "--out", str(out))
        assert_true("unsupported format fails", r3.returncode != 0)


def main():
    test_safe_filename()
    test_csv_seed()
    test_column_overrides()
    test_xlsx_input()
    test_dry_run_and_errors()
    print("\ntest_glossary_seed.py: all tests passed.")


if __name__ == "__main__":
    main()
