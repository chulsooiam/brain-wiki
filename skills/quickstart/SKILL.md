---
name: quickstart
description: >
  Guided first-run for a fresh brain-wiki vault: five skippable interview
  questions, then build — mode, scaffold, tiered source ingestion, requested
  pages. Resumable if interrupted. Triggers on: "/quickstart", "quick start",
  "guided setup", "help me set up this wiki", "interview setup", or when the
  wiki skill detects a fresh vault and the user accepts the offer.
allowed-tools: Read Write Edit Glob Grep Bash
---

# quickstart: Guided First-Run Interview

You run a short interview, then build the user's wiki from their answers.
Every question is skippable, every skip has a stated fallback, and the
skip-everything path must land exactly where a plain `/wiki` scaffold lands.
You orchestrate existing machinery — the `wiki` skill's scaffold,
`scripts/wiki-mode.py`, the corpus conversion pipeline, `scripts/corpus-tier.py`
— you do not invent new structure.

## 0. Gate and resume

Run `python3 scripts/quickstart-state.py status` first.

- `IN_PROGRESS` → summarize what is already answered/built (the status output
  lists it), then ask: **"You have an unfinished quickstart. Resume from where
  it stopped, or start over?"** Resume = continue at the first pending item.
  Start over = `begin --force`.
- `DONE` → say the vault already completed quickstart and route to the normal
  `wiki` skill flow. Do not re-run unless the user explicitly asks.
- `NOT_STARTED` → offer the gate question:

> This looks like a fresh vault. Want the guided quick start? Five short
> questions — all skippable — and I'll build the wiki from your answers,
> including ingesting a folder of sources if you have one. (yes / no — "no"
> continues with the standard one-question setup.)

On **no**: hand over to the `wiki` skill's normal setup. Nothing is written.
On **yes**: `python3 scripts/quickstart-state.py begin`, then interview.

## 1. The interview

Ask one question at a time. After each answer run
`quickstart-state.py answer KEY "<answer>"`; on a skip run
`quickstart-state.py skip KEY`. Never re-ask a question the state file already
holds. The questions, verbatim:

**Q1 (`purpose`)** — What is this wiki for? A research topic, a team's
knowledge base, a personal second brain, a project archive — one or two
sentences is plenty. This becomes the vault's north star: it decides what gets
a page and what gets left out. *(Skip — I'll start general and the purpose
will emerge from your first ingests.)*

**Q2 (`about`)** — Now tell me a bit about yourself — your work, your
interests, whatever shapes what this wiki should pay attention to. I'll use
this to judge what's worth a page and how to pitch the writing. *(Skip — the
wiki will learn who you are from what you feed it.)*

**Q3 (`mode`)** — How should the wiki be organized? **Generic** (type-based
folders: concepts, entities, sources — the default), **LYT** (maps of
content), **PARA** (projects/areas/resources/archive), or **Zettelkasten**
(atomic linked notes). If these names mean nothing to you, that's a sign
Generic is the right answer. *(Skip = Generic.)*

**Q4 (`sources`)** — Do you have a folder of documents the wiki should be
built from? Give me the path. If some sources are more authoritative than
others, you can give me two paths — a **tier-1 folder** (primary, trusted)
and a **tier-2 folder** (background, supporting) — and search results will
rank them accordingly. *(Skip — you can always drop files in later and say
"ingest this".)*

**Q5 (`pages`)** — Are there specific pages you already know you want — a
person, a project, a running meeting-notes register, a glossary? Name them
and I'll create them as the skeleton the ingested material fills in.
*(Skip — pages will emerge from the sources instead.)*

## 2. The build

Run the steps in order. After each completes, mark it:
`python3 scripts/quickstart-state.py step NAME`.

**`mode`** — `python3 scripts/wiki-mode.py set <mode>` (Q3 answer, lowercase;
skipped = `generic`, which is also satisfied by simply not writing mode.json —
still run `set generic` so the choice is explicit and resumable).

**`scaffold`** — run the `wiki` skill's scaffold using Q1 as the vault
description (skipped = a general-purpose description you state openly) and
the chosen mode's folder conventions. Seed `hot.md`, `index.md`, `log.md`,
`overview.md`. Then write `wiki/meta/quickstart-brief.md`: the five answers
(or "skipped") verbatim, dated — the vault's durable record of its founding
intent. Q2 informs the voice of `overview.md` and what you later judge
page-worthy; record it in the brief, not as a wiki page about the user unless
they asked for one in Q5.

**`sources`** — only if Q4 gave paths. For each folder, in this order:
1. Enumerate it: file count, formats, total size. **Show the user and get one
   confirmation before touching anything.** Folders contain things people
   forget are there.
2. Over 200 files → propose batched ingestion (convert + index now, ingest in
   rounds over later sessions) instead of silently grinding.
3. Copy-convert into `.sources/<Folder Name>/` — PDFs/DOCX/PPTX etc. through
   the conversion pipeline (`skills/wiki-ingest` + `scripts/convert.py`),
   Markdown copied as-is. **Never move, modify, or delete the originals.**
4. Register tiers: `python3 scripts/corpus-tier.py set "<Tier-1 Folder>"
   --tier tier1 --bonus 0.03` and tier-2 at `--bonus 0.015`. One folder = one
   tier at 0.03.
5. Build the corpus index (`scripts/corpus-index.py`, then BM25). If ollama /
   `nomic-embed-text` is unavailable, build BM25 only and **say so plainly**:
   keyword search works now, semantic rerank activates after
   `bash bin/setup-retrieve.sh`.

**`pages`** — only if Q5 named pages. Create each as a stub in the
mode-appropriate folder: a one-line purpose (inferred from the name and Q1),
an empty structure the ingest can fill, and an `index.md` link. Do not
fabricate content for them.

**`ingest`** — only if sources were converted. Run a first ingest pass per
the `wiki-ingest` skill over the tier-1 folder (or the single folder), within
the batch plan from step `sources`. Skeleton pages from Q5 are priority
targets for cross-references.

**`report`** — close out:
1. `python3 scripts/quickstart-state.py finish`
2. Update `hot.md` with what was built.
3. Tell the user, concretely: mode, folders created, sources converted (n of
   m, any failures named), tiers registered, pages created, what the first
   query commands are (`/wiki`, `/corpus-query`, `/combined-query`).
4. If `.obsidian/graph.json` is missing or unconfigured, end with: run
   `bash bin/setup-vault.sh` before opening Obsidian to get the pre-configured
   graph view.

## Guardrails

- **Copy-convert only.** The user's source folders are read-only to you.
- **No git operations without asking** — but do ask once, after `scaffold`:
  "Should this vault be a git repository? I'd keep it private — your source
  documents stay out of it either way (`.sources/` is gitignored)."
- Every skip's fallback is stated in the question itself; honor it exactly.
- An error mid-build is not a reason to start over: fix, re-run the step, and
  only then mark it. The state file is what makes half-built recoverable.
- Never write outside the vault directory.
