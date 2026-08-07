---
description: Query BOTH tiers — the curated wiki and the primary source corpus — and synthesize one answer with per-tier attribution and explicit discrepancies.
---

Read the `combined-query` skill, then answer the user's question from both
retrieval tiers.

Usage:
- `/combined-query [question]` — search both tiers, synthesize with attribution
- `/combined-query` with no argument — ask what they want to find

Workflow:

1. Run both tiers in one call, passing the question verbatim:

   ```bash
   python3 scripts/combined-retrieve.py "<question>" --top 5 --json
   ```

   Each tier self-heals a missing/stale index (default-on). Check per-tier
   `status`: mention `rebuilt+ok`, and if a tier is `unavailable` say so —
   never present a one-tier answer as a both-tier answer. Exit 10 = both
   tiers down; stop and say so.

2. `Read` the `absolute_path` of top candidates from BOTH lists — wiki pages
   for conclusions and `raw_file:` provenance, corpus documents for exact
   wording. Snippets are locators, never answers.

3. Answer in the fixed shape from the skill: **Answer / Wiki says / Sources
   say / Discrepancies**. The Discrepancies section is mandatory whenever
   the tiers conflict — name the wiki page, quote the source, state which to
   trust, and flag the page for wiki-lint.

4. Apply the corpus citation rules: no DOCX clause numbers as the
   original's; PPTX cited by "deck, Slide N"; transcripts as speaker
   statements with [mm:ss], not organizational positions.
