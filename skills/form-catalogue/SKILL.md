---
name: form-catalogue
description: "Catalogue spreadsheets and survey forms (XLSForm/ODK/Kobo, data workbooks, CSV) into a queryable form registry instead of ingesting them as prose. Deterministic stdlib profiling: question types, choice lists, languages, versions. Triggers on: catalogue forms, catalogue this spreadsheet, form registry, profile xlsform, index these forms, catalogue xlsx, spreadsheet inventory."
---

# form-catalogue: Spreadsheet & Survey-Form Cataloguing

A survey form with a 5,000-row choices sheet is one of the most *structured* artifacts an organization produces — and "ingesting it as prose" destroys exactly that structure while flooding the wiki with noise. Spreadsheets get **catalogued, not ingested**: one registry entry per file, holding the facts someone doing form-alignment work actually asks for. Which form is this? What does it collect? How many questions, in which languages, against which version?

**Division of labor:**

| stage | who | what |
|---|---|---|
| parse workbooks, count questions/choices, detect XLSForm, extract settings | `scripts/form-catalogue.py` | stdlib-only (no openpyxl), reproducible, testable |
| name the form's purpose, group related forms, link entities | the LLM (you) | judgment, from filenames + profile + (optionally) a look at label text |

This skill is the `catalogue` depth of [`skills/wiki-ingest/SKILL.md`](../wiki-ingest/SKILL.md) §Ingestion Depth, and follows the same transport / mode / locking rules as every writing skill.

---

## Profiling

```bash
python3 scripts/form-catalogue.py profile FILE...            # JSON per file
python3 scripts/form-catalogue.py registry FILE... --details # ready markdown
```

What comes back per file:

- **XLSForm** (a workbook with `survey` + `choices` sheets — the ODK/Kobo convention): question count and per-type breakdown (`select_one`, `integer`, `text`, …), group/repeat structure, choice-list count with total options and the largest lists, declared languages, and `settings` (form_title, form_id, version).
- **Generic workbook**: per-sheet dimensions and header row — enough to say what the data *is* without reading it.
- **CSV/TSV**: same as workbook.
- **Legacy `.xls`/`.doc`**: reported `unsupported-legacy`, never guessed at. Convert to `.xlsx` first if the file matters.
- Corrupt/unreadable files come back as `kind: error` inside the JSON — a batch never dies on one bad file.

The script only reads; it never writes into the vault.

---

## Registry pages

1. **Registry home** — `wiki/meta/form-registry.md`. Start from the `registry` command's markdown table, then add the judgment layer: a one-line *purpose* per form, grouping by form family, and wikilinks. Lock, write via transport, release.

2. **Form-family pages** — when several files are versions or country adaptations of the same instrument (recognizable from `form_id`, titles, or filename conventions), create one page per family under the mode-routed source path (`wiki-mode.py route source "<family name>"`), not one page per file. The family page lists its variants in a table: file, country/context, version, questions, languages, size. Frontmatter: `type: form-family`, plus `supersedes:`/`superseded_by:` between versions where the lineage is clear (see wiki-lint §Version Lineage).

3. **Cross-links** — a form implements questions, feeds datasets, and belongs to programs. Link the family page to the relevant entity/concept pages (the data-collection exercise, the standard it aligns to). If the corpus has a question-bank export, note which forms draw on it.

4. **Log** the operation in `wiki/log.md` per wiki-ingest step 10 (`catalogue` instead of `ingest` in the header line).

What the registry deliberately does NOT contain: the choice lists themselves, question-by-question listings, or any respondent data. The registry answers "which form, what shape, where" — the file itself remains the source of truth, linked by path.

> [!warning] Data files vs form definitions
> A workbook can be a form *definition* (structure, safe to catalogue in detail) or collected *data* (may contain personal information). Generic workbooks whose headers look like case records — names, phone numbers, locations at the individual level — get a registry line with path, dimensions, and purpose ONLY. Do not reproduce header samples or cell content for those; flag them `contains: microdata (not summarized)` so downstream users know why the entry is thin.

---

## Batch mode

For a folder of forms:

1. `profile` everything in one call; triage by `kind`.
2. Report the shape to the user before writing: N XLSForms (M questions total), N workbooks, N legacy, N errors — plus the family grouping you intend.
3. Build/update the registry and family pages, then one log entry.
4. Re-runs are cheap and idempotent: profiles are deterministic, so re-cataloguing after new form drops only changes what changed. Respect `.raw/.manifest.json` delta tracking for skip decisions.

---

## How to think (10-principle mapping)

See [`skills/think/SKILL.md`](../think/SKILL.md) for the canonical framework.

| # | Principle | Application here |
|---|-----------|-------------------|
| 1 | OBSERVE (ext) | Profile first. The JSON tells you what a file is before you form an opinion about it. |
| 2 | OBSERVE (int) | Am I about to "just quickly ingest" a spreadsheet? That instinct is the failure mode this skill exists to stop. |
| 3 | LISTEN | The user's real question is usually "which forms do I have and how do they differ" — alignment work, not content work. |
| 4 | THINK | Which files are one instrument in many versions? Family grouping is the main judgment call. |
| 5 | CONNECT (lat) | Forms ↔ question banks ↔ datasets ↔ standards. The registry is a hub, not a leaf. |
| 6 | CONNECT (sys) | `catalogue` depth from ingest-depth routes here; lineage frontmatter connects versions; lint keeps it honest. |
| 7 | FEEL | A thin, accurate registry beats a rich, wrong one. Especially near microdata. |
| 8 | ACCEPT | Legacy binaries stay unprofiled until converted. Say so; don't guess. |
| 9 | CREATE | Registry home + family pages with variant tables and cross-links. |
| 10 | GROW | Each new form drop refines families and versions; the registry compounds like the rest of the wiki. |
