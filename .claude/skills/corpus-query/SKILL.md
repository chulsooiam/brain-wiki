---
name: corpus-query
description: "Answer questions from the PRIMARY SOURCE documents under .sources/, not the curated wiki. Hybrid BM25 + cosine rerank over a separate tier-2 chunk index of the converted archive. Use when the wiki page is thin, missing, or you need the exact wording of an original document. Triggers on: what does the source say, quote the original, find the passage, search the sources, search the corpus, search the documents, exact wording, verbatim, cite the document, what does the policy actually say, is this in the source, corpus query, source query."
allowed-tools: Read Bash
---

# corpus-query: Query the Source Documents

`wiki-query` answers from what the vault **concluded**. This skill answers from
what a source document **actually said**.

Two independent tiers, neither touching the other's state:

| tier | covers | index | entry point |
|---|---|---|---|
| wiki (`wiki-retrieve`) | curated wiki pages | `.vault-meta/{chunks,bm25}` | `scripts/retrieve.py` |
| **corpus (this skill)** | converted source corpus under `.sources/` | `.vault-meta/corpus/` | `scripts/corpus-retrieve.py` |

Every corpus chunk carries a `tier` field derived from its top-level folder
under `.sources/`: a folder named "…Tier N…" becomes tier `N`, any other
folder becomes its slugified name (`Meeting Transcripts` →
`meeting-transcripts`), and `.vault-meta/corpus-tiers.json` can override
both the mapping and the per-tier ranking bonus
(`{"map": {folder: label}, "bonus": {label: additive}}`).

Ranking applies a tie-breaker-sized bonus toward authoritative tiers
(defaults: T1 +0.03, T0 +0.02, T2 +0.015 on the cosine scale) — it reorders
near-equals, never outranks a genuinely better match.

**Origin**: part of the professional-corpus extension; deliberately a
separate skill rather than an edit to `wiki-query`, so a toolkit update
cannot conflict with it.

---

## Data privacy

Fully on-machine. Chunk prefixes are the synthetic tier (document title +
folder + opening sentence) and rerank uses local ollama. **No document text
leaves the machine** at index or query time — unlike `wiki-retrieve`'s tier-1
and tier-2 prefix generators, this skill has no egress path at all.

---

## Feature detection

Run this before relying on the skill:

```bash
[ -f scripts/corpus-retrieve.py ] && [ -f .vault-meta/corpus/bm25/index.json ]
```

**Hybrid retrieval is default-on (2026-08-07).** A missing or stale index is
rebuilt inline before answering — no provisioning step. If retrieval prints an
`auto-build:` note on stderr, mention that the index was just (re)built. Exit
**10** now means the index could not be made ready at all (no `.sources/`
corpus, or the build failed) — on exit 10, say so plainly and fall back to
`wiki-query`; do not silently answer from the wiki tier and let the user
believe they got a source-document answer.

---

## Query

```bash
python3 scripts/corpus-retrieve.py "<the user's question verbatim>" --top 5
```

Output is JSON with `--json`, human-readable otherwise. Each candidate carries:

- `absolute_path` — the converted `.md`, ready for `Read`
- `page_path` — vault-relative, e.g. `.sources\Tier 1\...pdf.md`
- `chunk_index` — which chunk of that document matched
- `tier` — curation tier (`0`–`3`, `forms`, `transcripts`)
- `bm25_score`, `rerank_score`, and `final_score` (rerank + tier bonus)

Useful flags: `--bm25-top N` (candidates before rerank, default 20),
`--no-rerank` (skip ollama — faster, BM25 order), `--json`.

For a bare keyword lookup with no rerank round-trip:

```bash
python3 scripts/corpus-bm25.py query "terms" --top 10 --quiet
```

---

## Workflow

1. **Retrieve** — run the command above with the user's question verbatim.
2. **Read the sources** — `Read` the `absolute_path` of the top candidates.
   The snippet is a locator, never a substitute; a 300-character snippet is not
   enough to answer from and quoting it as if it were the document is how
   confident-sounding wrong answers happen.
3. **Answer with citation** — name the document and chunk. Prefer quoting the
   original wording, since that is the whole point of asking this tier.
4. **Note conflicts** — if the source contradicts a wiki page, say so
   explicitly. That is a genuinely valuable finding, not an inconvenience: it
   means the wiki has drifted from its evidence.

---

## Known limits — state these when they matter

**Clause numbers in converted documents are not always the source's own.**
The docling conversion fabricates hierarchical numbering on Word
auto-numbered documents — one measured policy document used Parts I–V with
continuous paragraphs and lettered sub-items, but the converted file
renders them as `1.1`–`5.5`. **Never cite a clause number from a converted
document as if it were the original's.** Quote the text and cite the
document; if the user needs a clause number, open the original via the
`raw_file:` pointer on the corresponding wiki page.

**Footnotes are often dropped**, including substantive ones (legal citations,
definitions). Absence of a footnote in the converted text is not evidence the
original lacked it.

**Some data tables were lost to `<!-- image -->` placeholders.** If a chunk
shows a placeholder where a table should be, the data is in the original only.

**Prefixes are the synthetic tier**, not the claude-cli tier the wiki index
uses. A chunk whose own text never names its subject is weaker here than its
wiki equivalent. Upgrading is a re-index, not a schema change.

**PPTX slide numbers ARE real citation anchors** — the opposite of DOCX
clause numbers. Converted decks carry `## Slide N — title` headings written
from the deck's actual slide order (python-pptx conversion), so cite decks as
"deck, Slide N". A slide showing mostly `<!-- image -->` is visual content;
check for a `**Slide description (vision):**` block, and if absent the
content exists only in the original deck.

**Meeting transcripts (if the corpus includes them) are verbatim speech**,
timestamped per speaker turn. Quote them as what a speaker SAID in a
meeting, not as an organizational position or a policy conclusion — and
prefer authoritative-tier documents when the two disagree.

**Excel workbooks are not in this index.** The document-vs-dataset policy
catalogues them (the catalogue itself is indexed); a question about workbook
CONTENTS needs the original file, whose path the catalogue gives you.

---

## Rebuild

**Usually unnecessary** — `corpus-retrieve.py` detects a missing/stale index
and rebuilds inline (default-on). The manual sequence, for forced rebuilds:

```bash
python3 scripts/corpus-index.py        # chunk (resumable; --rebuild forces)
python3 scripts/corpus-dedup.py --apply
python3 scripts/corpus-bm25.py build
```

Chunking runs in seconds at this corpus size. Step 1 skips unchanged chunks
by hash, so routine re-runs are cheap.

`corpus-index.py` applies two guards that the wiki tier does not need, both
because converted documents are structurally unlike hand-written pages:

- **A 4,000-char hard cap per chunk.** `chunk_body()` flushes only after a
  paragraph exceeds the target, and converted PDFs arrive as single unbroken
  blocks — the largest chunk here was 139,626 chars, 70x the target. Oversized
  chunks made `nomic-embed-text` return HTTP 500, so rerank silently fell back
  to BM25 order on ~7% of candidates.
- **Collapsed punctuation runs.** Table-of-contents dot leaders reached 116
  characters and tokenize into scores of tokens, overflowing the embedder's
  context even well under the character cap.

**Do not skip the dedup step.** A corpus can hold the same text under several
filenames — in the 2026-07 archive one 34 MB merged PDF produced 10.7% of all
chunks and crowded every result list until deduped. Duplicate chunk ids are
recorded in `.vault-meta/corpus/chunks-duplicates.json` (marked, not moved);
`corpus-bm25.py build` reads the manifest and excludes them. Delete the
manifest and rebuild to undo.

---

## Related

- `wiki-query` skill (from the brain-wiki plugin) — the vault-conclusions tier
- `wiki-retrieve` skill (from the brain-wiki plugin) — retrieval primitive this mirrors
