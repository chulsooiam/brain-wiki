#!/usr/bin/env python3
"""Hybrid retrieval over the tier-2 source corpus (2026-07-27).

Mirrors scripts/retrieve.py — BM25 over contextualized chunks, then cosine
rerank on nomic-embed-text vectors via local ollama — but over
.vault-meta/corpus/ instead of the wiki chunks.

Written as a thin driver rather than a rebind of retrieve.py because that
script resolves chunk files as VAULT_ROOT / hit["path"], and BM25 hit paths
are relative to CHUNKS_DIR.parent.parent — the vault root for the wiki tier,
but .vault-meta for the corpus tier. Reimplementing the short pipeline is more
honest than patching that assumption from outside.

The embedding cache is deliberately SHARED with the wiki tier: it is keyed by
text, so a passage embedded for one tier is free for the other.

Output JSON matches retrieve.py's shape so callers can treat the two tiers
alike: {query, strategy, candidates:[{absolute_path, snippet, bm25_score,
rerank_score, ...}]}.

Hybrid retrieval is DEFAULT-ON (2026-08-07): a missing or stale index is
rebuilt inline (chunk -> dedup -> BM25) before answering, instead of refusing
with exit 10. The chunker hash-skips unchanged documents, so a freshness pass
over this corpus costs seconds. --no-auto-build restores the old refusal so
scripted callers can distinguish "not provisioned". Exit 10 now means "no
index AND no .sources/ corpus to build one from".

Tier bonus: chunks carry the curation tier of their top-level source folder
(Tier 0-3 / forms / transcripts). A small additive bonus nudges authoritative
tiers up between near-equal candidates; it is tie-breaker sized on the cosine
scale (~0.03) and cannot outrank a genuinely better match.

Exit codes:
  0  — ok
  10 — not provisioned and nothing to build from (or build failed)

Usage:
    corpus-retrieve.py "query" [--top 5] [--bm25-top 20] [--no-rerank]
                       [--json] [--no-auto-build]
"""
import argparse
import importlib.util
import json
import os
import subprocess
import sys
import time
from pathlib import Path

VAULT_ROOT = Path(__file__).resolve().parent.parent
SRC = VAULT_ROOT / ".sources"
CORPUS_META = VAULT_ROOT / ".vault-meta" / "corpus"
CHUNKS = CORPUS_META / "chunks"
INDEX = CORPUS_META / "bm25" / "index.json"
EXIT_NOT_PROVISIONED = 10

# Tie-breaker-sized nudge toward authoritative tiers (rerank cosine scale).
# Defaults favour numeric tiers 1 > 0 > 2; .vault-meta/corpus-tiers.json may
# override or extend per label: {"bonus": {"<label>": 0.03, ...}}.
def _tier_bonus():
    bonus = {"1": 0.030, "0": 0.020, "2": 0.015}
    cfg = VAULT_ROOT / ".vault-meta" / "corpus-tiers.json"
    if cfg.exists():
        try:
            data = json.loads(cfg.read_text(encoding="utf-8"))
            for k, v in (data.get("bonus") or {}).items():
                bonus[str(k)] = float(v)
        except (OSError, ValueError, AttributeError, TypeError) as exc:
            print(f"warning: ignoring malformed corpus-tiers.json: {exc}",
                  file=sys.stderr)
    return bonus


TIER_BONUS = _tier_bonus()


def newest_source_mtime():
    newest = 0.0
    for root, dirs, files in os.walk(SRC):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for f in files:
            if f.lower().endswith(".md") and not f.startswith("."):
                try:
                    m = os.stat(os.path.join(root, f)).st_mtime
                except OSError:
                    continue
                if m > newest:
                    newest = m
    return newest


def ensure_index(auto_build=True):
    """Build/refresh the index inline so retrieval is default-on.

    Returns None when the index is ready, or an error string when it cannot
    be made ready (caller exits 10).
    """
    reason = None
    if not INDEX.is_file():
        reason = "missing"
    elif newest_source_mtime() > INDEX.stat().st_mtime:
        reason = "stale"
    if reason is None:
        return None
    if not SRC.is_dir():
        return f"index {reason} and no source corpus at {SRC}"
    if not auto_build:
        return (f"index {reason}; auto-build disabled — run corpus-index.py, "
                "corpus-dedup.py --apply, corpus-bm25.py build")

    print(f"auto-build: corpus index {reason} — rebuilding (default-on "
          "hybrid retrieval; hash-skip makes this cheap)", file=sys.stderr)
    env = dict(os.environ, PYTHONUTF8="1")
    for script_args in (["corpus-index.py"],
                        ["corpus-dedup.py", "--apply"],
                        ["corpus-bm25.py", "build"]):
        proc = subprocess.run(
            [sys.executable, str(VAULT_ROOT / "scripts" / script_args[0]),
             *script_args[1:]],
            capture_output=True, text=True, env=env,
            cwd=str(VAULT_ROOT), encoding="utf-8", errors="replace")
        if proc.returncode != 0:
            # Lock contention means another process is building the same
            # index right now. Don't fail the query — wait for the winner's
            # index to land, then use it.
            if proc.returncode == 1 or "lock" in proc.stderr.lower():
                print("auto-build: another build in progress — waiting",
                      file=sys.stderr)
                deadline = time.time() + 90
                while time.time() < deadline:
                    time.sleep(2)
                    if (INDEX.is_file()
                            and INDEX.stat().st_mtime >= newest_source_mtime()):
                        print("auto-build: index refreshed by the other "
                              "process", file=sys.stderr)
                        return None
            return (f"auto-build failed at {script_args[0]} "
                    f"(rc={proc.returncode}): {proc.stderr.strip()[-400:]}")
    print("auto-build: done", file=sys.stderr)
    return None


def load(name, filename):
    spec = importlib.util.spec_from_file_location(
        name, str(VAULT_ROOT / "scripts" / filename))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def short(page):
    for pre in (".sources\\", ".sources/"):
        if page.startswith(pre):
            return page[len(pre):]
    return page


def main():
    ap = argparse.ArgumentParser(
        description="Hybrid retrieval over the converted source corpus.")
    ap.add_argument("query")
    ap.add_argument("--top", type=int, default=5)
    ap.add_argument("--bm25-top", type=int, default=20)
    ap.add_argument("--no-rerank", action="store_true")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--allow-remote-ollama", action="store_true")
    ap.add_argument("--no-auto-build", action="store_true",
                    help="refuse (exit 10) instead of building a missing/"
                         "stale index inline")
    args = ap.parse_args()

    err = ensure_index(auto_build=not args.no_auto_build)
    if err:
        print(f"ERR: {err}", file=sys.stderr)
        return EXIT_NOT_PROVISIONED

    bm25 = load("bm25_index", "bm25-index.py")
    bm25.META_DIR = CORPUS_META
    bm25.CHUNKS_DIR = CHUNKS
    bm25.BM25_DIR = CORPUS_META / "bm25"
    bm25.INDEX_PATH = INDEX

    try:
        hits = bm25.query(args.query, top_k=args.bm25_top)
    except SystemExit:
        # A fresh-mtime but unreadable index (truncated write, disk fault)
        # exits inside the bm25 module. Self-heal once: force a rebuild by
        # dropping the corrupt file, then retry.
        if args.no_auto_build:
            raise
        print("auto-build: corpus index unreadable — rebuilding once",
              file=sys.stderr)
        try:
            INDEX.unlink()
        except OSError:
            pass
        err = ensure_index(auto_build=True)
        if err:
            print(f"ERR: {err}", file=sys.stderr)
            return EXIT_NOT_PROVISIONED
        hits = bm25.query(args.query, top_k=args.bm25_top)
    print(f"bm25: {len(hits)} hits", file=sys.stderr)

    candidates = []
    for h in hits:
        # hit paths are relative to CHUNKS_DIR.parent.parent == .vault-meta
        try:
            c = json.loads((CHUNKS.parent.parent / h["path"])
                           .read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        page = c.get("page_path", "")
        candidates.append({
            "chunk_id": h["chunk_id"],
            "page_address": c.get("page_address"),
            "page_path": page,
            "document": page.replace("\\", "/").split("/")[-1],
            "absolute_path": str(VAULT_ROOT / page),
            "chunk_index": c.get("chunk_index"),
            "tier": c.get("tier", ""),
            "bm25_score": h["score"],
            "score": h["score"],
            "path": h["path"],   # rerank.load_chunk() resolves this itself
            "snippet": " ".join(c.get("raw_text", "").split())[:300],
        })

    strategy = "bm25-only"
    if not args.no_rerank and candidates:
        reranker = load("rerank", "rerank.py")
        # load_chunk() resolves VAULT_ROOT / candidate["path"] at CALL time and
        # corpus hit paths are relative to .vault-meta. EMBED_CACHE_PATH was
        # already computed at import, so the cache stays shared with the wiki
        # tier — a passage embedded once is free for both.
        reranker.VAULT_ROOT = VAULT_ROOT / ".vault-meta"
        # Over-fetch so the tier bonus can reorder near-equals at the cut line.
        candidates = reranker.rerank(args.query, candidates,
                                     top_k=min(args.top + 5, len(candidates)),
                                     allow_remote=args.allow_remote_ollama)
        strategy = "bm25+rerank:" + (candidates[0].get("rerank_source", "?")
                                     if candidates else "?")
        for c in candidates:
            base = c.get("rerank_score")
            if not isinstance(base, float):
                base = 0.0
            c["final_score"] = base + TIER_BONUS.get(c.get("tier", ""), 0.0)
        candidates.sort(key=lambda c: c["final_score"], reverse=True)
        candidates = candidates[: args.top]
    else:
        # BM25 scores are unbounded, so normalize before adding the bonus.
        top_score = max((c["bm25_score"] for c in candidates), default=1.0) or 1.0
        for c in candidates:
            c["final_score"] = (c["bm25_score"] / top_score
                                + TIER_BONUS.get(c.get("tier", ""), 0.0))
        candidates.sort(key=lambda c: c["final_score"], reverse=True)
        candidates = candidates[: args.top]

    if args.json:
        print(json.dumps({"query": args.query, "strategy": strategy,
                          "top_k": args.top, "candidates": candidates},
                         indent=2, ensure_ascii=False))
        return 0

    print(f"query: {args.query}   [{strategy}]   {len(candidates)} results\n")
    for c in candidates:
        rs = c.get("rerank_score")
        head = f"{rs:.3f}" if isinstance(rs, float) else f"{c['bm25_score']:.1f}"
        tier = c.get("tier") or "?"
        print(f"[{head}]  [T{tier}]  {short(c['page_path'])}  "
              f"(chunk {c['chunk_index']}, bm25 {c['bm25_score']:.1f})")
        print(f"         {c['snippet'][:200]}…\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
