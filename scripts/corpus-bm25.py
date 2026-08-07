#!/usr/bin/env python3
"""BM25 build/query for the tier-2 source corpus (2026-07-27).

Reuses scripts/bm25-index.py verbatim — same k1/b, same tokenizer, same
scoring — but rebinds its module-level path constants to .vault-meta/corpus/
so the wiki tier's chunks and index are never touched.

Rebinding beats forking: a future change to the ranking function is picked up
automatically instead of silently diverging between the two tiers.

Usage:
    corpus-bm25.py build
    corpus-bm25.py query "text" [--top N] [--quiet]
    corpus-bm25.py stats
"""
import argparse
import importlib.util
import json
import sys
from pathlib import Path

VAULT_ROOT = Path(__file__).resolve().parent.parent
CORPUS_META = VAULT_ROOT / ".vault-meta" / "corpus"
DUPE_MANIFEST = CORPUS_META / "chunks-duplicates.json"


def load_duplicates():
    """Chunk ids corpus-dedup.py marked as redundant copies.

    The manifest is advisory: a stale id simply never matches a chunk on
    disk. Missing manifest means nothing is excluded.
    """
    if not DUPE_MANIFEST.is_file():
        return set()
    try:
        data = json.loads(DUPE_MANIFEST.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        print(f"WARN: unreadable {DUPE_MANIFEST.name} ({e}); "
              f"indexing every chunk.", file=sys.stderr)
        return set()
    return set(data.get("duplicate_chunk_ids", []))


def load_bm25():
    spec = importlib.util.spec_from_file_location(
        "bm25_index", str(VAULT_ROOT / "scripts" / "bm25-index.py"))
    mod = importlib.util.module_from_spec(spec)
    sys.modules["bm25_index"] = mod
    spec.loader.exec_module(mod)

    mod.META_DIR = CORPUS_META
    mod.CHUNKS_DIR = CORPUS_META / "chunks"
    mod.BM25_DIR = CORPUS_META / "bm25"
    mod.INDEX_PATH = mod.BM25_DIR / "index.json"
    mod.LOCK_PATH = CORPUS_META / ".bm25.lock"
    # Duplicates are filtered here rather than moved out of chunks/, so the
    # chunk store stays complete for corpus-index.py and prune_corpus_chunks.py.
    mod.EXCLUDE_CHUNK_IDS = load_duplicates()
    CORPUS_META.mkdir(parents=True, exist_ok=True)
    mod.LOCK_PATH.touch(exist_ok=True)
    return mod


def main():
    ap = argparse.ArgumentParser(description="BM25 over the tier-2 corpus.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("build")
    q = sub.add_parser("query")
    q.add_argument("text")
    q.add_argument("--top", type=int, default=10)
    q.add_argument("--quiet", action="store_true", help="Paths only")
    sub.add_parser("stats")
    args = ap.parse_args()

    m = load_bm25()

    if args.cmd == "build":
        fd = m.acquire_lock()
        try:
            index = m.build_index()
            if index is None:
                print("Nothing to index.")
                return 1
            m.write_index(index)
            print(f"Wrote {m.INDEX_PATH}  docs={index['doc_count']}  "
                  f"vocab={len(index['vocab'])}  avg_dl={index['avg_dl']:.1f}"
                  f"  excluded={len(m.EXCLUDE_CHUNK_IDS)}")
        finally:
            m.release_lock(fd)
        return 0

    if args.cmd == "query":
        # bm25-index.py returns the CHUNK FILE path, which tells a reader
        # nothing. Resolve each hit back to its source document.
        for r in m.query(args.text, top_k=args.top):
            page, idx, snippet = "?", "?", ""
            try:
                rec = json.loads(
                    (m.CHUNKS_DIR.parent.parent / r["path"])
                    .read_text(encoding="utf-8"))
                page = rec.get("page_path", "?")
                idx = rec.get("chunk_index", "?")
                snippet = " ".join(rec.get("raw_text", "").split())[:150]
            except (OSError, ValueError):
                pass
            for pre in (".sources\\", ".sources/"):
                if page.startswith(pre):
                    page = page[len(pre):]
            print(f"{r['score']:8.3f}  {page}  [chunk {idx}]")
            if snippet and not args.quiet:
                print(f"          {snippet}…")
        return 0

    idx = m.load_index()
    print(json.dumps({k: v for k, v in idx.items()
                      if k not in ("vocab", "postings", "docs")},
                     indent=2)[:800])
    return 0


if __name__ == "__main__":
    sys.exit(main())
