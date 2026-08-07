---
description: Query the curated wiki (the vault's CONCLUSIONS tier) with hybrid retrieval. For source-document wording use /corpus-query; for both tiers use /combined-query.
---

Read the `brain-wiki:wiki-query` plugin skill, then answer the user's
question from the curated wiki.

Usage:
- `/wiki-query [question]` — search the wiki tier and answer with citations
- `/wiki-query` with no argument — ask what they want to find

Notes on top of the plugin skill:

1. Retrieval entry point (hybrid BM25 + rerank over wiki chunks):

   ```bash
   python3 scripts/retrieve.py "<question>" --top 5
   ```

   Hybrid retrieval is default-on: a missing or stale index rebuilds inline
   (synthetic prefixes only, no egress) — mention it if an `auto-build:`
   note appears on stderr. Exit 10 now means there are no wiki pages to
   index or the build itself failed; only then fall back to the legacy
   hot→index→drill read order, and say so. Forced manual rebuild:

   ```bash
   python3 scripts/contextual-prefix.py --all --no-llm && python3 scripts/bm25-index.py build
   ```

2. This tier answers what the vault CONCLUDED. If the user needs the exact
   wording of a source document, or wants the wiki claim verified against
   its evidence, switch to `/corpus-query` or `/combined-query` and say why.

3. Every wiki source page carries `raw_file:` provenance — check
   `raw_file_confidence` before trusting it, and prefer quoting the
   converted source over paraphrasing when the user will cite externally.
