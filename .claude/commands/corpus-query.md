---
description: Search the PRIMARY SOURCE documents under .sources/ (converted PDFs/DOCX), not the curated wiki. Hybrid BM25 + cosine rerank, fully on-machine.
---

Read the `corpus-query` skill, then answer the user's question from the source
documents.

Usage:
- `/corpus-query [question]` — search and answer with citations
- `/corpus-query` with no argument — ask what they want to find

Workflow:

1. Run the retrieval, passing the user's question verbatim:

   ```bash
   python3 scripts/corpus-retrieve.py "<question>" --top 5
   ```

   A missing or stale index rebuilds inline (default-on hybrid retrieval) —
   mention it if an `auto-build:` note appears. Exit 10 means the index could
   not be made ready at all. Say so and stop — do NOT quietly answer from the
   wiki instead and let the user believe they got a source-document answer.

2. **Read the documents.** `Read` the `absolute_path` of the top hits. The
   300-character snippet is a locator, not an answer; answering from it is how
   confident-sounding wrong answers happen. Chunks overlap by 200 chars, so
   chunk n±1 usually completes a cut-off thought.

3. **Answer with citation** — name the document and chunk index, and prefer
   quoting the original wording, since that is the point of asking this tier
   rather than `/wiki`.

4. If nothing relevant comes back, retry once with `--bm25-top 60 --top 10`
   before concluding the corpus lacks the material. Recall is lexical: the
   reranker only reorders what BM25 already found, so a paraphrase that shares
   no vocabulary with the documents will miss. Prefer the corpus's own
   terminology — the exact program names, policy identifiers, and technical
   terms its documents use.

Citation rules — these are conversion defects, not source defects:

- **Never cite a clause number** from a converted document as if it were the
  original's. The converter fabricates hierarchical numbering on Word
  auto-numbered documents (measured: Part/paragraph schemes rendered as
  `1.1`–`5.5`). Quote the text, cite the document. For a real clause number,
  open the original via the `raw_file:` pointer on the corresponding wiki
  page.
- **Footnotes were frequently dropped**, including substantive ones. Their
  absence in the converted text is not evidence the original lacked them.
- **`<!-- image -->` marks lost content**, sometimes entire data tables. If a
  hit shows a placeholder where a table belongs, the data exists only in the
  original.

Scope is all of `.sources/` except dot-directories (conversion bookkeeping,
backups). Excel workbooks are catalogued, not indexed — a question about
workbook contents needs the original file, whose path the catalogue gives
you.

For what the vault *concluded* rather than what a source *said*, use the
`wiki-query` skill instead.
