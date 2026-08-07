---
name: combined-query
description: "Answer questions from BOTH retrieval tiers at once — the curated wiki (conclusions) AND the primary source corpus under .sources/ — then synthesize one answer with per-tier attribution and explicit discrepancy reporting. The default query for open questions. Triggers on: combined query, ask both, check wiki and sources, full search, everything we have on, what do we know overall, verify against sources, cross-check the wiki, combined search."
allowed-tools: Read Bash
---

# combined-query: Ask Both Tiers, Answer Once

`wiki-query` answers from what the vault **concluded**. `corpus-query`
answers from what a source document **actually said**. This skill runs both
and synthesizes — with every claim attributed to its tier, because the two
can disagree, and *that disagreement is the most valuable thing this skill
can find*.

**Origin**: part of the professional-corpus extension; not in upstream
v1.9.x.

---

## Retrieve

One call runs both tiers (each retriever self-heals a missing/stale index —
default-on hybrid retrieval, in `retrieve.py` and `corpus-retrieve.py`
respectively):

```bash
python3 scripts/combined-retrieve.py "<the user's question verbatim>" --top 5 --json
```

Output: `{query, tiers: {wiki: {status, strategy, candidates}, corpus: {…}}}`.

- Scores are **per-tier and not comparable across tiers** (different prefix
  quality). Never merge the two candidate lists by score.
- `status` per tier: `ok`, `rebuilt+ok` (index was just rebuilt inline —
  mention it), or `unavailable` (with a reason in `note`).
- Corpus candidates carry `tier` — the curation tier of the source's
  top-level folder (see the `corpus-query` skill), already factored into
  ranking as a small bonus.
- Exit 10 = both tiers unavailable. Say so; answer from general knowledge
  ONLY if the user explicitly accepts that.

## Read

`Read` the `absolute_path` of the top candidates from **both** lists.
Snippets are locators, not answers. Wiki pages give the synthesis and its
`raw_file:` provenance; corpus documents give the exact wording.

## Answer — fixed shape

> **Answer** — the synthesis, built from both tiers.
>
> **Wiki says** — conclusions, cited by page. Omit the section (say why) if
> the tier was unavailable or silent on the question.
>
> **Sources say** — documents, cited by name + chunk; PPTX by "deck, Slide
> N" (slide numbers are real anchors); transcripts as "<meeting>, [mm:ss]"
> and attributed as what a speaker SAID, not an organizational position.
>
> **Discrepancies** — REQUIRED whenever the tiers conflict. Name the wiki
> page, quote the source, state which you'd trust (usually the source — the
> wiki page has drifted) and flag the page for a wiki-lint fix. If nothing
> conflicts, drop the section.

Never present a one-tier answer as a both-tier answer: if a tier was down or
empty, the answer must say which tier it actually came from.

## Citation rules (inherited)

From `corpus-query`, and they apply here unchanged: never cite a DOCX clause
number from a converted document as the original's; dropped footnotes are
not evidence of absence; `<!-- image -->` means the content exists only in
the original; Excel workbook contents are catalogued, not indexed.

## When to prefer the single-tier skills

- `wiki-query` — "what did we conclude / where is X filed" (navigation,
  synthesis, speed).
- `corpus-query` — "quote the original / exact wording" (verbatim needs).
- **This skill** — open questions, anything the user will cite externally,
  and any "is the wiki right about…" verification.
