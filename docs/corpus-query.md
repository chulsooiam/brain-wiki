# The Corpus Layer — two-tier retrieval and the three-query surface

The professional-corpus extension adds a second retrieval tier to the vault
and a conversion pipeline to feed it. This is the architecture reference;
the skills (`corpus-convert`, `corpus-query`, `combined-query`) are the
operating instructions.

## Two tiers, one rule of thumb

| tier | covers | state | entry point |
|---|---|---|---|
| **wiki** (stock) | curated wiki pages | `.vault-meta/{chunks,bm25}` | `scripts/retrieve.py` |
| **corpus** (extension) | converted source documents under `.sources/` | `.vault-meta/corpus/{chunks,bm25}` | `scripts/corpus-retrieve.py` |

Ask the wiki tier what the vault **concluded**; ask the corpus tier what a
**source document actually said**. The tiers never touch each other's state,
and their scores are never comparable (different prefix quality) — result
lists are kept separate all the way up.

## The three-query surface

- `/wiki-query` — conclusions tier (`scripts/retrieve.py`).
- `/corpus-query` — sources tier (`scripts/corpus-retrieve.py`).
- `/combined-query` — both (`scripts/combined-retrieve.py`), synthesized in
  the fixed shape *Answer / Wiki says / Sources say / Discrepancies*, with
  tier contradictions surfaced loudly (each one is a free wiki-lint finding).

## Default-on hybrid retrieval

There is no provisioning step. Both retrievers self-heal: a missing or stale
index rebuilds inline before the query runs (announced via an `auto-build:`
note on stderr; `--no-auto-build` restores the old exit-10 refusal). The
rebuild uses synthetic prefixes only — deterministic, on-machine, zero
egress. Rerank uses local ollama (`nomic-embed-text`) and degrades to BM25
order, labeled, when ollama is down.

The only deliberate opt-in left is **egress**: LLM contextual prefixes
(`contextual-prefix.py` without `--no-llm`) send document text to an API and
must always be an explicit user decision.

## The pipeline, end to end

```
source tree ── scripts/convert.py ──▶ .sources/  (Markdown mirror + QA flags)
                                          │  first /corpus-query
                                          ▼
             corpus-index.py ─▶ corpus-dedup.py ─▶ corpus-bm25.py build
                                          │
                                          ▼
             corpus-retrieve.py  (BM25 → cosine rerank → tier bonus)
```

- **Conversion** (`convert.py` + `convert_formats.py`): per-format engines —
  docling for PDF/DOCX (OCR off by default; `CONVERT_OCR=1` re-enables),
  python-pptx for PPTX (slide anchors + speaker notes), markdownify for
  HTML, header-aware parsing for EML/MSG, policy-skip for spreadsheets.
  `.convert_flags.jsonl` records what needs a finishing layer.
- **Tier metadata**: each chunk is stamped with the curation tier of its
  top-level `.sources/` folder — "…Tier N…" → `N`, anything else → its
  slugified name; `.vault-meta/corpus-tiers.json` overrides the mapping and
  the per-tier ranking bonus (`{"map": {...}, "bonus": {...}}`).
- **Chunking parity**: `corpus-index.py` imports the wiki tier's own
  `chunk_body()` so both tiers split text identically. Two corpus-only
  guards exist because converted documents are structurally unlike
  hand-written pages: a 4,000-char hard cap per chunk (converted PDFs arrive
  as single unbroken blocks; oversized chunks made the embedder fail and
  rerank silently degrade) and collapsed punctuation runs (TOC dot leaders
  overflow the embedder's context).
- **Dedup is load-bearing**: `corpus-dedup.py` marks duplicate chunks in
  `.vault-meta/corpus/chunks-duplicates.json` (marked, never moved);
  `corpus-bm25.py build` excludes them. In one measured archive a single
  merged PDF produced 10.7% of all chunks until deduped.
- **Shared embedding cache**: `.vault-meta/embed-cache.json` is keyed by
  text — a passage embedded for one tier is free for the other.

## The rebind contract

`corpus-bm25.py` and `corpus-retrieve.py` do not fork the stock BM25/rerank
code — they import `bm25-index.py` and `rerank.py` as modules and **rebind**
their state constants (`META_DIR`, `CHUNKS_DIR`, `BM25_DIR`, `LOCK_PATH`,
and rerank's `VAULT_ROOT`) to `.vault-meta/corpus/`. Ranking improvements in
the stock scripts propagate to the corpus tier for free. The cost: renaming
those constants in `bm25-index.py`/`rerank.py` breaks the corpus tier —
`tests/test_corpus_layer.py` guards the contract.

## Local-patch surface (what the drift check greps for)

Several stock scripts carry local hardening that a careless upstream merge
would silently revert. If a session-start check reports
`LOCAL PATCHES REVERTED`, re-apply from git history:

| token | file | patch |
|---|---|---|
| `_meta_lock_dir_acquire` | `scripts/wiki-lock.sh` | portable mkdir-lock fallback where flock is unavailable (Git Bash) |
| `msvcrt` | `tiling-check.py`, `bm25-index.py`, `rerank.py` | Windows file-locking shim |
| `EXCLUDE_CHUNK_IDS` | `scripts/bm25-index.py` | dedup-manifest exclusion at build time |
| `duplicate_chunk_ids` | `scripts/corpus-dedup.py` | marked-not-moved duplicate manifest |
| `stale-removed` | `scripts/corpus-index.py` | stale-chunk sweep on re-index |

## Claude Desktop (MCP)

`scripts/corpus-mcp-server.py` is a dependency-free JSON-RPC-over-stdio MCP
server exposing `search_corpus` and `read_document` (confined to
`.sources/` — an MCP server is callable by any prompt in the app, so it must
not become an arbitrary-file-read tool). Register it in
`claude_desktop_config.json` to query the corpus from Claude Desktop.

## Privacy stance

Conversion, indexing, and BM25 are fully local. Rerank calls local ollama
only (`--allow-remote-ollama` exists but is off by default). Nothing in the
corpus layer sends document text off-machine unless you explicitly enable
LLM contextual prefixes. Emails (.eml/.msg) deserve an explicit decision
before they enter the corpus at all — they carry third-party personal
content.
