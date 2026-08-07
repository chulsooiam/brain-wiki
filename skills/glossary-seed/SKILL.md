---
name: glossary-seed
description: "Seed entity stubs from an acronym/term table (CSV or Excel) BEFORE bulk ingestion, so every later page links into a stable jargon graph instead of inventing entities ad hoc. Idempotent; never overwrites. Triggers on: seed glossary, seed the acronyms, glossary from spreadsheet, seed entity stubs, acronym list, bootstrap entities, seed terms."
---

# glossary-seed: Entity Stubs from a Term Table

Institutional corpora are jargon-dense: acronyms, program codes, component names. When entity pages get invented ad hoc mid-ingestion, the same term lands under three spellings and cross-linking quietly degrades. If the corpus contains an acronym list or glossary spreadsheet — most institutional collections do — seed it **first**. Every page written afterwards links into a stable set of entity targets from day one.

**Run order matters**: glossary-seed → then bulk `wiki-ingest` / `transcript-distill`. Seeding after the fact still works (stubs merge into the link graph) but loses the main benefit.

---

## Workflow

1. **Dry-run first**, always:
   ```bash
   python3 scripts/glossary-seed.py TABLE.xlsx --dry-run
   ```
   Columns are auto-detected from headers (`term`/`acronym`/`abbreviation`/… and `definition`/`expansion`/`meaning`/…); non-English or unusual headers need `--term-col` / `--def-col` (header name or 0-based index). Review the term list and count. A wrong column choice at this stage would seed hundreds of garbage pages.

2. **Route the output** through the vault's mode: for `generic` mode the default (`wiki/entities/`) is correct; otherwise ask `python3 scripts/wiki-mode.py route entity "X"` for the right folder and pass `--out`.

3. **Seed**:
   ```bash
   python3 scripts/glossary-seed.py TABLE.xlsx --date YYYY-MM-DD
   ```
   One stub per term (frontmatter: `entity_type: term`, `glossary_seed: true`) plus a `glossary.md` index page, alphabetical, one line per term. Duplicate rows are collapsed (first definition wins); filename-hostile characters are sanitized with the original term preserved as the page title and link alias.

4. **Log** the seeding in `wiki/log.md` (one entry for the whole batch, not per term).

**Idempotency guarantee**: existing pages are never overwritten (no `--force` in normal operation). A stub that `wiki-ingest` later expanded stays expanded; re-running after the source table grows adds only the new terms. This is what makes it safe to re-seed every time the organization updates its acronym list.

## What the stubs are — and are not

A seeded stub is a *link target with a definition*, not knowledge. Do not bulk-expand stubs speculatively; `wiki-ingest` expands each one the first time a source actually discusses it. `wiki-lint`'s orphan check should treat `glossary_seed: true` pages linked only from `glossary.md` as expected, not as orphan findings — they are pre-positioned, not abandoned.

---

## How to think (10-principle mapping)

See [`skills/think/SKILL.md`](../think/SKILL.md) for the canonical framework.

| # | Principle | Application here |
|---|-----------|-------------------|
| 1 | OBSERVE (ext) | Dry-run and read the term list before seeding. Garbage in the table = garbage graph. |
| 3 | LISTEN | The org's own acronym list is authoritative for *its* jargon — better than any inference. |
| 6 | CONNECT (sys) | Seed → ingest → expand. The graph compounds because the targets existed first. |
| 8 | ACCEPT | Stubs are supposed to be thin. Resist filling them without sources. |
| 10 | GROW | Re-seed on every table update; only the delta lands. |
