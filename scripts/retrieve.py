#!/usr/bin/env python3
"""retrieve.py — hybrid retrieval orchestrator for the Compound Vault.

Pipeline (v1.7):
  query  →  bm25-index.py query (top-K candidates by BM25 over contextualized chunks)
         →  rerank.py        (cosine on nomic-embed-text vectors via ollama,
                              or no-op if ollama unavailable)
         →  drill            (return chunk pages with absolute paths so the
                              caller can Read them and synthesize)

Loads sibling scripts as Python modules (no subprocess overhead). Hybrid
retrieval is DEFAULT-ON: a missing or stale index self-heals inline before
the query runs (synthetic prefixes via contextual-prefix.py --no-llm, then a
BM25 build — zero egress). Fallback behaviour:
- Index missing/stale, wiki pages exist        → auto-build inline, then answer
                                                  (note on stderr; suppress with
                                                  --no-auto-build to get exit 10).
- No wiki pages at all, or auto-build fails    → exit 10 with friendly message;
                                                  caller falls back to v1.6 legacy
                                                  hot→index→drill read order.
- If rerank stage cannot embed (no ollama)     → no-op rerank, returns BM25 order.

Output schema (JSON to stdout):
{
  "query": "...",
  "strategy": "bm25+rerank:cosine:nomic-embed-text" | "bm25+noop-rerank",
  "top_k": 5,
  "candidates": [
    {
      "chunk_id": "c-000042:3",
      "page_address": "c-000042",
      "page_path": "wiki/concepts/Foo.md",
      "absolute_path": "/abs/path/to/wiki/concepts/Foo.md",
      "chunk_index": 3,
      "bm25_score": 7.12,
      "rerank_score": 0.81,
      "rerank_source": "cosine:nomic-embed-text",
      "snippet": "... first 200 chars of the chunk ..."
    },
    ...
  ]
}

Usage:
  retrieve.py "your query here"           # standard: BM25 top-20, rerank to top-5
  retrieve.py "query" --top 10            # change result count
  retrieve.py "query" --no-rerank         # skip rerank, BM25-only
  retrieve.py "query" --explain           # include per-stage diagnostics

Exit codes:
  0 — success
  2 — usage error
  10 — feature not provisioned (no chunks or no BM25 index); caller falls back
"""

import argparse
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

VAULT_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = VAULT_ROOT / "scripts"
META_DIR = VAULT_ROOT / ".vault-meta"
CHUNKS_DIR = META_DIR / "chunks"
BM25_INDEX = META_DIR / "bm25" / "index.json"
WIKI_DIR = VAULT_ROOT / "wiki"

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_NOT_PROVISIONED = 10


def log(msg):
    print(msg, file=sys.stderr)


def import_sibling(name, filename):
    """Import a hyphenated sibling .py file as a Python module.

    Wrapped in try/except (v1.7.2; closes audit M5) so a syntax error or
    missing dependency in a sibling helper produces a friendly diagnostic
    instead of a bare Python traceback at the user's first retrieve call.
    """
    target = SCRIPTS_DIR / filename
    if not target.is_file():
        log(f"ERR: sibling helper {filename} not found at {target}")
        log("  Run `bash bin/setup-retrieve.sh --check` to verify the install.")
        sys.exit(EXIT_NOT_PROVISIONED)
    try:
        spec = importlib.util.spec_from_file_location(name, target)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    except (ImportError, SyntaxError, AttributeError) as e:
        log(f"ERR: failed to import sibling helper {filename}: {type(e).__name__}: {e}")
        log("  This likely means the helper script is corrupted or has a syntax error.")
        log("  Run `python3 scripts/<helper>.py --help` directly to see the underlying error.")
        log("  If it persists: re-clone the repo or check `git status` for local damage.")
        sys.exit(EXIT_NOT_PROVISIONED)


def _newest_wiki_mtime():
    newest = 0.0
    for root, dirs, files in os.walk(WIKI_DIR):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for f in files:
            if f.lower().endswith(".md"):
                try:
                    m = os.stat(os.path.join(root, f)).st_mtime
                except OSError:
                    continue
                newest = max(newest, m)
    return newest


def _wiki_has_pages():
    for root, dirs, files in os.walk(WIKI_DIR):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        if any(f.lower().endswith(".md") for f in files):
            return True
    return False


def ensure_index(auto_build=True):
    """Default-on self-heal: (re)build a missing or stale wiki index inline.

    Mirrors corpus-retrieve.py's ensure_index so both tiers behave the same
    whether called directly or through combined-retrieve.py. Exits 10 only
    when there is nothing to index or the build itself fails.
    """
    reason = None
    if not BM25_INDEX.is_file():
        reason = "missing"
    elif not CHUNKS_DIR.is_dir() or not any(CHUNKS_DIR.iterdir()):
        reason = "missing"
    elif _newest_wiki_mtime() > BM25_INDEX.stat().st_mtime:
        reason = "stale"
    if reason is None:
        return

    if not _wiki_has_pages():
        log("ERR: no wiki pages exist yet — nothing to index. Create pages "
            "(or run an ingest) and retrieval will provision itself.")
        sys.exit(EXIT_NOT_PROVISIONED)
    if not auto_build:
        log(f"ERR: wiki index {reason} and --no-auto-build given. Run "
            "`python3 scripts/contextual-prefix.py --all --no-llm && "
            "python3 scripts/bm25-index.py build`.")
        sys.exit(EXIT_NOT_PROVISIONED)

    log(f"auto-build: wiki index {reason} — rebuilding (synthetic prefixes, "
        "no egress)")
    env = dict(os.environ, PYTHONUTF8="1")
    for cmd in ([str(SCRIPTS_DIR / "contextual-prefix.py"), "--all", "--no-llm"],
                [str(SCRIPTS_DIR / "bm25-index.py"), "build"]):
        proc = subprocess.run([sys.executable, *cmd], capture_output=True,
                              text=True, encoding="utf-8", errors="replace",
                              env=env, cwd=str(VAULT_ROOT))
        if proc.returncode != 0:
            log(f"ERR: wiki auto-build failed at {Path(cmd[0]).name} "
                f"(rc={proc.returncode}): {proc.stderr.strip()[-300:]}")
            sys.exit(EXIT_NOT_PROVISIONED)


def chunk_snippet(chunk_data, max_chars=200):
    text = chunk_data.get("raw_text", "")
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rstrip() + "…"


def main():
    parser = argparse.ArgumentParser(description="Hybrid retrieval over the vault.")
    parser.add_argument("query", help="Natural-language query")
    parser.add_argument("--top", type=int, default=5, help="Final result count (post-rerank)")
    parser.add_argument("--bm25-top", type=int, default=20,
                        help="Candidate count from BM25 (pre-rerank)")
    parser.add_argument("--no-rerank", action="store_true",
                        help="Skip the rerank stage; return BM25-only")
    parser.add_argument("--explain", action="store_true",
                        help="Include per-stage diagnostics in output")
    parser.add_argument("--allow-remote-ollama", action="store_true",
                        help="Forwarded to rerank.py")
    parser.add_argument("--no-auto-build", action="store_true",
                        help="Refuse (exit 10) instead of rebuilding a "
                             "missing/stale index inline")
    args = parser.parse_args()

    ensure_index(auto_build=not args.no_auto_build)

    bm25 = import_sibling("bm25_index", "bm25-index.py")
    reranker = import_sibling("rerank", "rerank.py")

    bm25_hits = bm25.query(args.query, top_k=args.bm25_top)
    log(f"bm25: {len(bm25_hits)} hits")

    candidates = []
    for h in bm25_hits:
        chunk_path = VAULT_ROOT / h["path"]
        try:
            chunk = json.loads(chunk_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        candidates.append({
            "chunk_id": h["chunk_id"],
            "page_address": chunk.get("page_address"),
            "page_path": chunk.get("page_path"),
            "absolute_path": str((VAULT_ROOT / chunk.get("page_path", "")).resolve()),
            "chunk_index": chunk.get("chunk_index"),
            "bm25_score": h["score"],
            "path": h["path"],
            "snippet": chunk_snippet(chunk),
        })

    if args.no_rerank:
        final = candidates[:args.top]
        strategy = "bm25-only"
        for c in final:
            c["rerank_score"] = c["bm25_score"]
            c["rerank_source"] = "skipped"
    else:
        final = reranker.rerank(
            args.query, candidates, top_k=args.top,
            allow_remote=args.allow_remote_ollama,
        )
        # Derive strategy from first candidate's rerank_source
        first_src = (final[0].get("rerank_source") if final else "unknown")
        strategy = f"bm25+rerank:{first_src}"

    # Dedupe by page (we may have multiple chunks of the same page; collapse to best)
    by_page = {}
    for c in final:
        addr = c.get("page_address")
        if addr not in by_page or c.get("rerank_score", 0) > by_page[addr].get("rerank_score", 0):
            by_page[addr] = c
    deduped = list(by_page.values())
    deduped.sort(key=lambda c: c.get("rerank_score", 0), reverse=True)

    out = {
        "query": args.query,
        "strategy": strategy,
        "top_k": args.top,
        "candidates": deduped[:args.top],
    }
    if args.explain:
        out["explain"] = {
            "bm25_candidate_count": len(bm25_hits),
            "post_rerank_count": len(final),
            "deduped_count": len(deduped),
            "bm25_top_param": args.bm25_top,
        }

    print(json.dumps(out, indent=2, ensure_ascii=False))
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
