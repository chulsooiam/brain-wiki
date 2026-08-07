#!/usr/bin/env python3
"""form-catalogue.py — deterministic spreadsheet/XLSForm profiler.

Spreadsheets are the format standard ingestion handles worst: a survey form
with a 5,000-row choices sheet "ingested as prose" floods a wiki with noise
while losing exactly the structure that made it valuable. The form-catalogue
skill (skills/form-catalogue/SKILL.md) catalogues spreadsheets instead of
ingesting them; this script is its deterministic half.

Reads .xlsx/.xlsm workbooks with the stdlib only (zipfile + ElementTree —
no openpyxl/pandas dependency), plus .csv/.tsv. Legacy binary .xls/.doc-era
formats are reported as `unsupported-legacy`, never guessed at.

XLSForm awareness: a workbook whose sheets include `survey` and `choices`
(the ODK/Kobo XLSForm convention used across humanitarian data collection)
is profiled as a form: question counts by type, group/repeat structure,
choice lists with option counts, declared languages, and the
`settings` sheet (form_title, form_id, version). Anything else is profiled
as a generic workbook: per-sheet dimensions and header row.

CLI:
  form-catalogue.py profile FILE...            # JSON profile per file
  form-catalogue.py registry FILE...           # markdown registry table
  form-catalogue.py registry FILE... --details # + per-form detail blocks

Output is data for the catalogue skill — the skill writes the wiki pages.
This script never writes into the vault.

Exit codes:
  0 — success (per-file errors are reported inside the JSON, not fatal)
  2 — usage error
"""

import argparse
import csv
import json
import re
import sys
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

NS = {
    "m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "pr": "http://schemas.openxmlformats.org/package/2006/relationships",
}

XLSFORM_STRUCTURAL = {"begin_group", "end_group", "begin_repeat", "end_repeat",
                      "begin group", "end group", "begin repeat", "end repeat"}
XLSFORM_NON_QUESTION = XLSFORM_STRUCTURAL | {"note"}

LANG_COL = re.compile(r"^(?:label|hint|constraint_message|required_message)"
                      r"::\s*(?P<lang>.+?)\s*$", re.IGNORECASE)

MAX_CELLS_PER_SHEET = 2_000_000  # sanity cap against pathological files

EXIT_OK = 0
EXIT_USAGE = 2


def col_to_index(ref):
    """'C5' → 0-based column index 2."""
    letters = "".join(ch for ch in ref if ch.isalpha())
    idx = 0
    for ch in letters.upper():
        idx = idx * 26 + (ord(ch) - ord("A") + 1)
    return idx - 1


def _cell_text(cell, shared):
    t = cell.get("t", "n")
    if t == "s":
        v = cell.find("m:v", NS)
        try:
            return shared[int(v.text)] if v is not None else ""
        except (ValueError, IndexError):
            return ""
    if t == "inlineStr":
        return "".join(el.text or "" for el in cell.iter(f"{{{NS['m']}}}t"))
    v = cell.find("m:v", NS)
    return v.text if v is not None and v.text is not None else ""


def read_xlsx(path):
    """Return {sheet_name: rows} where rows is a list of lists of str."""
    with zipfile.ZipFile(path) as z:
        # Shared strings (optional member).
        shared = []
        if "xl/sharedStrings.xml" in z.namelist():
            root = ET.fromstring(z.read("xl/sharedStrings.xml"))
            for si in root.findall("m:si", NS):
                shared.append("".join(el.text or ""
                                      for el in si.iter(f"{{{NS['m']}}}t")))
        # Sheet name → target file, via workbook + rels.
        wb = ET.fromstring(z.read("xl/workbook.xml"))
        rels = ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))
        rid_to_target = {
            rel.get("Id"): rel.get("Target").lstrip("/")
            for rel in rels.findall("pr:Relationship", NS)
        }
        sheets = {}
        for sheet in wb.iter(f"{{{NS['m']}}}sheet"):
            name = sheet.get("name")
            rid = sheet.get(f"{{{NS['r']}}}id")
            target = rid_to_target.get(rid, "")
            member = target if target.startswith("xl/") else f"xl/{target}"
            if member not in z.namelist():
                continue
            root = ET.fromstring(z.read(member))
            rows = []
            cells_seen = 0
            for row in root.iter(f"{{{NS['m']}}}row"):
                out = []
                for cell in row.findall("m:c", NS):
                    ref = cell.get("r", "")
                    idx = col_to_index(ref) if ref else len(out)
                    while len(out) < idx:
                        out.append("")
                    out.append(_cell_text(cell, shared))
                    cells_seen += 1
                rows.append(out)
                if cells_seen > MAX_CELLS_PER_SHEET:
                    break
            sheets[name] = rows
        return sheets


def read_delimited(path):
    delim = "\t" if Path(path).suffix.lower() == ".tsv" else ","
    with open(path, newline="", encoding="utf-8-sig", errors="replace") as fh:
        return {"(csv)": list(csv.reader(fh, delimiter=delim))}


def header_map(rows):
    """First non-empty row → {lowercased header: column index}."""
    for row in rows:
        if any(c.strip() for c in row):
            return {c.strip().lower(): i for i, c in enumerate(row) if c.strip()}, row
    return {}, []


def find_sheet(sheets, wanted):
    for name in sheets:
        if name.strip().lower() == wanted:
            return name
    return None


def profile_xlsform(sheets):
    survey_name = find_sheet(sheets, "survey")
    choices_name = find_sheet(sheets, "choices")
    settings_name = find_sheet(sheets, "settings")

    survey = sheets[survey_name]
    hdr, header_row = header_map(survey)
    type_col = hdr.get("type")

    q_types = {}
    groups = repeats = questions = 0
    for row in survey[1:]:
        t = (row[type_col].strip() if type_col is not None and len(row) > type_col
             else "")
        if not t:
            continue
        base = t.split()[0].lower()
        full = t.lower()
        if full in XLSFORM_STRUCTURAL or base in {"begin_group", "begin_repeat",
                                                  "end_group", "end_repeat"}:
            if "repeat" in full:
                repeats += 0 if full.startswith("end") else 1
            else:
                groups += 0 if full.startswith("end") else 1
            continue
        key = "select_one" if base == "select_one" else \
              "select_multiple" if base == "select_multiple" else base
        q_types[key] = q_types.get(key, 0) + 1
        if full != "note":
            questions += 1

    # Extract from the original-case header row so "English (en)" survives.
    languages = sorted({m.group("lang") for h in header_row
                        for m in [LANG_COL.match(h.strip())] if m})

    lists = {}
    if choices_name:
        choices = sheets[choices_name]
        chdr, _ = header_map(choices)
        lcol = chdr.get("list_name", chdr.get("list name"))
        if lcol is not None:
            for row in choices[1:]:
                if len(row) > lcol and row[lcol].strip():
                    lists[row[lcol].strip()] = lists.get(row[lcol].strip(), 0) + 1

    settings = {}
    if settings_name:
        srows = sheets[settings_name]
        shdr, _ = header_map(srows)
        for row in srows[1:]:
            if any(c.strip() for c in row):
                for key, idx in shdr.items():
                    if len(row) > idx and row[idx].strip():
                        settings[key] = row[idx].strip()
                break

    return {
        "kind": "xlsform",
        "questions": questions,
        "question_types": dict(sorted(q_types.items(),
                                      key=lambda kv: -kv[1])),
        "groups": groups,
        "repeats": repeats,
        "choice_lists": len(lists),
        "choice_options": sum(lists.values()),
        "largest_lists": dict(sorted(lists.items(),
                                     key=lambda kv: -kv[1])[:5]),
        "languages": languages,
        "settings": {k: settings[k] for k in
                     ("form_title", "form_id", "version") if k in settings},
        "sheets": {name: len(rows) for name, rows in sheets.items()},
    }


def profile_workbook(sheets):
    out = {}
    for name, rows in sheets.items():
        _hdr, header_row = header_map(rows)
        out[name] = {
            "rows": len(rows),
            "cols": max((len(r) for r in rows), default=0),
            "header": [c for c in header_row[:12] if c.strip()],
        }
    return {"kind": "workbook", "sheet_profiles": out,
            "sheets": {name: len(rows) for name, rows in sheets.items()}}


def profile_file(path):
    p = Path(path)
    base = {"file": p.name, "path": str(p), "bytes": None}
    if not p.is_file():
        return {**base, "kind": "error", "error": "no such file"}
    base["bytes"] = p.stat().st_size
    ext = p.suffix.lower()
    try:
        if ext in (".xlsx", ".xlsm"):
            sheets = read_xlsx(p)
            lowered = {s.strip().lower() for s in sheets}
            if "survey" in lowered and "choices" in lowered:
                return {**base, **profile_xlsform(sheets)}
            return {**base, **profile_workbook(sheets)}
        if ext in (".csv", ".tsv"):
            return {**base, **profile_workbook(read_delimited(p))}
        if ext in (".xls", ".ods", ".doc"):
            return {**base, "kind": "unsupported-legacy",
                    "note": "convert to .xlsx to profile"}
        return {**base, "kind": "unsupported", "note": f"extension {ext}"}
    except (zipfile.BadZipFile, ET.ParseError, KeyError, OSError) as exc:
        return {**base, "kind": "error", "error": f"{type(exc).__name__}: {exc}"}


def render_registry(profiles, details=False):
    lines = [
        "| file | kind | questions | choice lists (options) | languages | size |",
        "|---|---|---|---|---|---|",
    ]
    for pr in profiles:
        size = f"{pr['bytes']:,} B" if pr.get("bytes") is not None else "—"
        if pr["kind"] == "xlsform":
            langs = ", ".join(pr["languages"]) or "—"
            lines.append(
                f"| {pr['file']} | XLSForm | {pr['questions']} "
                f"| {pr['choice_lists']} ({pr['choice_options']}) "
                f"| {langs} | {size} |")
        elif pr["kind"] == "workbook":
            nsheets = len(pr["sheets"])
            nrows = sum(pr["sheets"].values())
            lines.append(f"| {pr['file']} | workbook ({nsheets} sheets, "
                         f"{nrows} rows) | — | — | — | {size} |")
        else:
            lines.append(f"| {pr['file']} | {pr['kind']} | — | — | — | {size} |")
    if details:
        for pr in profiles:
            if pr["kind"] != "xlsform":
                continue
            lines.append("")
            lines.append(f"### {pr['file']}")
            s = pr.get("settings", {})
            if s:
                lines.append("- settings: " + ", ".join(f"{k}={v}"
                                                        for k, v in s.items()))
            lines.append(f"- questions: {pr['questions']} "
                         f"(types: " + ", ".join(f"{k}×{v}" for k, v in
                                                 pr["question_types"].items()) + ")")
            lines.append(f"- structure: {pr['groups']} groups, "
                         f"{pr['repeats']} repeats")
            if pr["largest_lists"]:
                lines.append("- largest choice lists: " +
                             ", ".join(f"{k} ({v})" for k, v in
                                       pr["largest_lists"].items()))
    return "\n".join(lines) + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    p_prof = sub.add_parser("profile", help="JSON profile per file")
    p_prof.add_argument("files", nargs="+")

    p_reg = sub.add_parser("registry", help="markdown registry table")
    p_reg.add_argument("files", nargs="+")
    p_reg.add_argument("--details", action="store_true")

    args = parser.parse_args(argv)
    profiles = [profile_file(f) for f in args.files]

    if args.command == "profile":
        print(json.dumps(profiles if len(profiles) > 1 else profiles[0],
                         indent=2, ensure_ascii=False))
    else:
        sys.stdout.write(render_registry(profiles, details=args.details))
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
