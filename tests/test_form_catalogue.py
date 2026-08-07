#!/usr/bin/env python3
"""test_form_catalogue.py — hermetic tests for scripts/form-catalogue.py.

Builds a minimal synthetic .xlsx (zipfile + hand-written sheet XML — the same
stdlib surface the reader uses) so no real workbook or third-party library is
needed. Covers: column-ref math, shared/inline strings, XLSForm detection and
profiling (question types, groups/repeats, choice lists, languages, settings),
generic workbook profiling, csv, legacy/unsupported/corrupt handling, and the
markdown registry rendering. No network, no LLM.

Usage:
  python3 tests/test_form_catalogue.py
"""
import importlib.util
import json
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HELPER = ROOT / "scripts" / "form-catalogue.py"

spec = importlib.util.spec_from_file_location("form_catalogue", HELPER)
fc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fc)


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


M = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PR = "http://schemas.openxmlformats.org/package/2006/relationships"


def sheet_xml(rows):
    """rows: list of list of str → minimal worksheet XML with inline strings."""
    body = []
    for ri, row in enumerate(rows, start=1):
        cells = []
        for ci, val in enumerate(row):
            col = ""
            n = ci + 1
            while n:
                n, rem = divmod(n - 1, 26)
                col = chr(65 + rem) + col
            cells.append(
                f'<c r="{col}{ri}" t="inlineStr"><is><t>{val}</t></is></c>')
        body.append(f'<row r="{ri}">{"".join(cells)}</row>')
    return (f'<worksheet xmlns="{M}"><sheetData>'
            + "".join(body) + "</sheetData></worksheet>")


def build_xlsx(path, sheets):
    """sheets: {name: rows}. Writes a minimal xlsx the reader can parse."""
    names = list(sheets)
    wb_sheets = "".join(
        f'<sheet name="{n}" sheetId="{i+1}" r:id="rId{i+1}"/>'
        for i, n in enumerate(names))
    workbook = (f'<workbook xmlns="{M}" xmlns:r="{R}">'
                f'<sheets>{wb_sheets}</sheets></workbook>')
    rels = f'<Relationships xmlns="{PR}">' + "".join(
        f'<Relationship Id="rId{i+1}" Type="{R}/worksheet" '
        f'Target="worksheets/sheet{i+1}.xml"/>'
        for i in range(len(names))) + "</Relationships>"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("xl/workbook.xml", workbook)
        z.writestr("xl/_rels/workbook.xml.rels", rels)
        for i, n in enumerate(names):
            z.writestr(f"xl/worksheets/sheet{i+1}.xml", sheet_xml(sheets[n]))


XLSFORM_SHEETS = {
    "survey": [
        ["type", "name", "label::English (en)", "label::French (fr)"],
        ["start", "start", "", ""],
        ["begin_group", "g1", "Group 1", "Groupe 1"],
        ["text", "resp_name", "Respondent name", "Nom"],
        ["select_one yn", "displaced", "Displaced?", "Déplacé?"],
        ["select_multiple needs", "needs", "Priority needs", "Besoins"],
        ["integer", "hh_size", "Household size", "Taille"],
        ["note", "n1", "A note", "Une note"],
        ["end_group", "", "", ""],
        ["begin_repeat", "r1", "Members", "Membres"],
        ["text", "member", "Member", "Membre"],
        ["end_repeat", "", "", ""],
    ],
    "choices": [
        ["list_name", "name", "label::English (en)"],
        ["yn", "yes", "Yes"],
        ["yn", "no", "No"],
        ["needs", "water", "Water"],
        ["needs", "shelter", "Shelter"],
        ["needs", "food", "Food"],
    ],
    "settings": [
        ["form_title", "form_id", "version"],
        ["Site Assessment", "site_assess_v2", "2.0"],
    ],
}


def test_col_to_index():
    assert_eq("A→0", 0, fc.col_to_index("A1"))
    assert_eq("C→2", 2, fc.col_to_index("C5"))
    assert_eq("AA→26", 26, fc.col_to_index("AA10"))


def test_xlsform_profile():
    with tempfile.TemporaryDirectory() as tmp:
        f = Path(tmp) / "form.xlsx"
        build_xlsx(f, XLSFORM_SHEETS)
        pr = fc.profile_file(f)
        assert_eq("kind xlsform", "xlsform", pr["kind"])
        # start, text, select_one, select_multiple, integer, text(repeat) = 6
        assert_eq("questions", 6, pr["questions"])
        assert_eq("select_one counted", 1, pr["question_types"]["select_one"])
        assert_eq("note counted as type not question", 1,
                  pr["question_types"]["note"])
        assert_eq("groups", 1, pr["groups"])
        assert_eq("repeats", 1, pr["repeats"])
        assert_eq("choice lists", 2, pr["choice_lists"])
        assert_eq("choice options", 5, pr["choice_options"])
        assert_eq("languages", ["English (en)", "French (fr)"], pr["languages"])
        assert_eq("settings title", "Site Assessment",
                  pr["settings"]["form_title"])
        assert_eq("settings version", "2.0", pr["settings"]["version"])


def test_generic_workbook():
    with tempfile.TemporaryDirectory() as tmp:
        f = Path(tmp) / "data.xlsx"
        build_xlsx(f, {"Tracker": [["id", "status"], ["1", "open"], ["2", "closed"]]})
        pr = fc.profile_file(f)
        assert_eq("kind workbook", "workbook", pr["kind"])
        assert_eq("rows", 3, pr["sheet_profiles"]["Tracker"]["rows"])
        assert_eq("header", ["id", "status"],
                  pr["sheet_profiles"]["Tracker"]["header"])


def test_shared_strings():
    with tempfile.TemporaryDirectory() as tmp:
        f = Path(tmp) / "shared.xlsx"
        workbook = (f'<workbook xmlns="{M}" xmlns:r="{R}"><sheets>'
                    f'<sheet name="S" sheetId="1" r:id="rId1"/></sheets></workbook>')
        rels = (f'<Relationships xmlns="{PR}"><Relationship Id="rId1" '
                f'Type="{R}/worksheet" Target="worksheets/sheet1.xml"/></Relationships>')
        shared = (f'<sst xmlns="{M}"><si><t>hello</t></si>'
                  f'<si><r><t>ri</t></r><r><t>ch</t></r></si></sst>')
        sheet = (f'<worksheet xmlns="{M}"><sheetData>'
                 f'<row r="1"><c r="A1" t="s"><v>0</v></c>'
                 f'<c r="B1" t="s"><v>1</v></c>'
                 f'<c r="D1"><v>42</v></c></row></sheetData></worksheet>')
        with zipfile.ZipFile(f, "w") as z:
            z.writestr("xl/workbook.xml", workbook)
            z.writestr("xl/_rels/workbook.xml.rels", rels)
            z.writestr("xl/sharedStrings.xml", shared)
            z.writestr("xl/worksheets/sheet1.xml", sheet)
        sheets = fc.read_xlsx(f)
        assert_eq("shared string", "hello", sheets["S"][0][0])
        assert_eq("rich-run shared string concatenated", "rich", sheets["S"][0][1])
        assert_eq("skipped cell padded", "", sheets["S"][0][2])
        assert_eq("numeric cell", "42", sheets["S"][0][3])


def test_csv_and_unsupported():
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        c = tmp / "codes.csv"
        c.write_text("code,name\nA,Alpha\nB,Beta\n", encoding="utf-8")
        pr = fc.profile_file(c)
        assert_eq("csv kind", "workbook", pr["kind"])
        assert_eq("csv rows", 3, pr["sheet_profiles"]["(csv)"]["rows"])

        x = tmp / "old.xls"
        x.write_bytes(b"\xd0\xcf\x11\xe0old-binary")
        assert_eq("legacy xls", "unsupported-legacy", fc.profile_file(x)["kind"])

        bad = tmp / "broken.xlsx"
        bad.write_bytes(b"not a zip at all")
        pr = fc.profile_file(bad)
        assert_eq("corrupt xlsx → error kind", "error", pr["kind"])

        assert_eq("missing file → error", "error",
                  fc.profile_file(tmp / "nope.xlsx")["kind"])


def test_registry_cli():
    with tempfile.TemporaryDirectory() as tmp:
        f = Path(tmp) / "form.xlsx"
        build_xlsx(f, XLSFORM_SHEETS)
        r = subprocess.run(
            [sys.executable, str(HELPER), "registry", str(f), "--details"],
            capture_output=True, text=True, encoding="utf-8")
        assert_eq("registry exit 0", 0, r.returncode)
        assert_true("registry table row",
                    "| form.xlsx | XLSForm | 6 | 2 (5) |" in r.stdout, r.stdout)
        assert_true("registry details block", "### form.xlsx" in r.stdout)
        assert_true("registry settings line",
                    "form_title=Site Assessment" in r.stdout)

        r2 = subprocess.run(
            [sys.executable, str(HELPER), "profile", str(f)],
            capture_output=True, text=True, encoding="utf-8")
        data = json.loads(r2.stdout)
        assert_eq("profile CLI questions", 6, data["questions"])


def main():
    test_col_to_index()
    test_xlsform_profile()
    test_generic_workbook()
    test_shared_strings()
    test_csv_and_unsupported()
    test_registry_cli()
    print("\ntest_form_catalogue.py: all tests passed.")


if __name__ == "__main__":
    main()
