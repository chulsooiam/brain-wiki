#!/usr/bin/env python3
"""Run BOTH retrieval tiers for /combined-query (2026-08-07).

Executes the wiki tier (scripts/retrieve.py) and the corpus tier
(scripts/corpus-retrieve.py) for the same query and emits one JSON document
with the two result sets kept SEPARATE. Scores are never interleaved across
tiers — the wiki tier carries different prefix quality than the corpus tier,
so cross-tier score comparison is not calibrated. Synthesis and attribution
(Answer / Wiki says / Sources say / Discrepancies) happen in the calling
skill, not here.

Default-on hybrid retrieval: a missing or stale index on EITHER tier is
rebuilt inline by that tier's own retriever (retrieve.py and
corpus-retrieve.py both self-heal; this script just runs them). A tier whose
retriever printed an `auto-build:` note is reported as status "rebuilt+ok".
A tier that still cannot answer is reported as status "unavailable" with a
reason — the caller must SAY which tier answered, never silently degrade.

Usage:
    combined-retrieve.py "query" [--top 5] [--no-rerank] [--json]

Exit 0 if at least one tier answered; exit 10 if both are unavailable.
"""
import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

VAULT_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = VAULT_ROOT / "scripts"
ENV = dict(os.environ, PYTHONUTF8="1")


def run(cmd):
    return subprocess.run(
        [sys.executable, *cmd], capture_output=True, text=True,
        encoding="utf-8", errors="replace", env=ENV, cwd=str(VAULT_ROOT))


def query_tier(name, script, query, top, no_rerank, extra=()):
    cmd = [str(SCRIPTS / script), query, "--top", str(top), *extra]
    if no_rerank:
        cmd.append("--no-rerank")
    proc = run(cmd)
    if proc.returncode != 0:
        return {"status": "unavailable", "note": proc.stderr.strip()[-300:],
                "strategy": None, "candidates": []}
    try:
        data = json.loads(proc.stdout)
    except ValueError:
        return {"status": "unavailable",
                "note": f"{name} tier returned unparseable output",
                "strategy": None, "candidates": []}
    # Both retrievers self-heal and announce it on stderr; surface that.
    build_note = next((ln.strip() for ln in proc.stderr.splitlines()
                       if ln.strip().startswith("auto-build:")), "")
    return {"status": "rebuilt+ok" if build_note else "ok",
            "note": build_note,
            "strategy": data.get("strategy"),
            "candidates": data.get("candidates", [])}


def main():
    ap = argparse.ArgumentParser(
        description="Query both retrieval tiers; never interleave scores.")
    ap.add_argument("query")
    ap.add_argument("--top", type=int, default=5, help="results PER TIER")
    ap.add_argument("--no-rerank", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    # Both tiers self-heal internally (default-on hybrid retrieval).
    wiki = query_tier("wiki", "retrieve.py", args.query, args.top,
                      args.no_rerank)
    corpus = query_tier("corpus", "corpus-retrieve.py", args.query, args.top,
                        args.no_rerank, extra=["--json"])

    result = {"query": args.query, "top_per_tier": args.top,
              "tiers": {"wiki": wiki, "corpus": corpus}}

    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        for name in ("wiki", "corpus"):
            t = result["tiers"][name]
            print(f"=== {name} tier [{t['status']}] "
                  f"{t.get('strategy') or ''} {t.get('note') or ''}")
            for c in t["candidates"]:
                rs = c.get("rerank_score")
                head = (f"{rs:.3f}" if isinstance(rs, float)
                        else f"{c.get('bm25_score', 0):.1f}")
                tier_tag = f"[T{c['tier']}] " if c.get("tier") else ""
                print(f"  [{head}] {tier_tag}{c.get('page_path')} "
                      f"(chunk {c.get('chunk_index')})")
            print()

    both_dead = all(result["tiers"][n]["status"] == "unavailable"
                    for n in ("wiki", "corpus"))
    return 10 if both_dead else 0


if __name__ == "__main__":
    sys.exit(main())
