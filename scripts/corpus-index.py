#!/usr/bin/env python3
"""Chunk the converted source corpus into a tier-2 index (2026-07-27).

Stock wiki-retrieve indexes the curated wiki PAGES. The converted documents
under .sources/ — the conversion-pipeline output of Wiki BASE Compact — are
the primary text that it never sees. This builds a SEPARATE chunk set for
them under .vault-meta/corpus/, so the wiki tier's own state is never touched
and the two can be queried independently.

Scope is all of .sources/: the top-level folder is the curation tier and is
stamped into every chunk as `tier`. Folders named "...Tier N..." map to "N";
any other folder maps to its slugified name; an optional
.vault-meta/corpus-tiers.json overrides both ({"map": {folder: label},
"bonus": {label: additive}}). corpus-retrieve.py applies a small
authoritative-tier-first bonus at ranking time.
Dot-directories (conversion bookkeeping, backups) are skipped.

Chunking imports the vault's own chunk_body() from contextual-prefix.py rather
than reimplementing it, so a corpus chunk and a wiki chunk are split
identically and their scores stay comparable.

Prefixes are the synthetic tier — deterministic, on-machine, no egress and no
API cost. Nothing here sends document text anywhere.

Resumable: a chunk whose body_hash and page_body_hash both match is skipped.

Usage:
    corpus-index.py [--rebuild] [--limit N]

Then: corpus-dedup.py --apply && corpus-bm25.py build
"""
import argparse
import glob
import hashlib
import importlib.util
import io
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

VAULT_ROOT = Path(__file__).resolve().parent.parent
SRC = VAULT_ROOT / ".sources"
CORPUS_META = VAULT_ROOT / ".vault-meta" / "corpus"
CHUNKS = CORPUS_META / "chunks"
SCHEMA_VERSION = 1

# chunk_body() flushes only AFTER a paragraph pushes it past the target, so a
# single huge paragraph becomes a single huge chunk. Hand-written wiki pages
# never do that; converted PDFs do constantly — flattened tables and OCR'd
# pages arrive as one unbroken block. Measured on this corpus: 7.2% of chunks
# over 6,000 chars, the largest 139,626 (70x the target).
#
# That breaks reranking outright. nomic-embed-text returns HTTP 500 above
# ~5,000 chars of input (verified: 5,000 ok, 6,000 fails), so every oversized
# chunk silently fell back to BM25 order. It also skews BM25 length
# normalization and makes a "chunk" useless as a citable unit.
#
# 4,000 leaves room for the ~400-char prefix inside that ceiling.
MAX_RAW_CHARS = 4000


def wp(p):
    """Windows extended-length path: this corpus exceeds MAX_PATH in places."""
    ap = os.path.abspath(str(p))
    if os.name == "nt" and not ap.startswith("\\\\?\\"):
        return "\\\\?\\" + ap
    return ap


def log(msg):
    print(time.strftime("[%H:%M:%S] ") + msg, flush=True)


def load_sibling(name, filename):
    """Import a sibling script whose filename contains a hyphen."""
    spec = importlib.util.spec_from_file_location(
        name, str(VAULT_ROOT / "scripts" / filename))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def sha256(text):
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def address_for(rel):
    """Stable per-path address. The 'src-' namespace keeps these visibly
    distinct from the wiki's 'c-'/'syn-' addresses."""
    return "src-" + hashlib.sha1(rel.encode("utf-8")).hexdigest()[:8]


def corpus_prefix(title, folder, body):
    """Synthetic tier, corpus flavour: title + folder + opening sentence.
    Re-injects document-level vocabulary into every chunk, which is what gives
    BM25 its lift on chunks whose own text never names the subject."""
    first = re.split(r"(?<=[.!?])\s+", body.strip(), maxsplit=1)
    opening = first[0][:300] if first else ""
    where = f" (filed under {folder})" if folder else ""
    return (f'This passage is from the source document "{title}"{where}. '
            f"The document opens: {opening}")


def normalize_filler(text):
    """Collapse long runs of repeated punctuation.

    Converted PDFs carry table-of-contents dot leaders — runs of up to 116 '.'
    in this corpus. Character count hides how bad that is: each run tokenizes
    into scores of tokens, so a 4,300-char TOC chunk overflowed
    nomic-embed-text's context and returned HTTP 500 even though it was well
    under the character cap. Collapsing the runs took that chunk to 1,861
    chars and it embedded fine.

    This is a quality fix, not just a workaround: leaders carry no meaning for
    BM25 or for embeddings, and the vault's own QA prompt already tells
    reviewers to ignore them.
    """
    return re.sub(r"([.\-_=·~*])\1{3,}", r"\1\1\1", text)


def split_oversized(chunks, limit=MAX_RAW_CHARS):
    """Break chunks over `limit` on sentence boundaries, hard-slicing only when
    a single sentence is itself too long (tables flattened to one line do this).
    Chunks at or under the limit pass through untouched, so the common case is
    identical to the wiki tier's output."""
    out = []
    for text in chunks:
        if len(text) <= limit:
            out.append(text)
            continue
        # Buffer is per-chunk so sentences never merge across a boundary the
        # paragraph chunker deliberately drew.
        buf = ""
        for piece in re.split(r"(?<=[.!?])\s+", text):
            if not piece.strip():
                continue
            if len(piece) > limit:
                if buf:
                    out.append(buf)
                    buf = ""
                for i in range(0, len(piece), limit):
                    out.append(piece[i:i + limit])
            elif not buf:
                buf = piece
            elif len(buf) + 1 + len(piece) <= limit:
                buf += " " + piece
            else:
                out.append(buf)
                buf = piece
        if buf:
            out.append(buf)
    return out


def strip_suffix(name):
    for suf in (".pdf.md", ".docx.md", ".doc.md", ".pptx.md", ".xlsx.md",
                ".csv.md", ".html.md", ".md"):
        if name.lower().endswith(suf):
            return name[: -len(suf)]
    return name


# Tier assignment is generic with an optional per-vault override:
#   .vault-meta/corpus-tiers.json  {"map": {"<top folder>": "<label>", ...},
#                                   "bonus": {"<label>": 0.03, ...}}
# Default rule: a top-level folder whose name contains "Tier N" is tier "N";
# any other folder becomes its slugified name ("Meeting Transcripts" ->
# "meeting-transcripts"). Files directly at the .sources/ root get no tier.
_TIER_RE = re.compile(r"tier\s*(\d+)", re.I)


def _tier_overrides():
    cfg = VAULT_ROOT / ".vault-meta" / "corpus-tiers.json"
    if cfg.exists():
        try:
            data = json.loads(cfg.read_text(encoding="utf-8"))
            return {str(k).lower(): str(v)
                    for k, v in (data.get("map") or {}).items()}
        except (OSError, ValueError, AttributeError, TypeError) as exc:
            print(f"warning: ignoring malformed corpus-tiers.json: {exc}",
                  file=sys.stderr)
    return {}


_TIER_OVERRIDES = _tier_overrides()


def tier_for(rel):
    rel = rel.replace("\\", "/")
    if "/" not in rel:
        return ""
    top = rel.split("/")[0]
    override = _TIER_OVERRIDES.get(top.lower())
    if override is not None:
        return override
    m = _TIER_RE.search(top)
    if m:
        return m.group(1)
    return re.sub(r"[^a-z0-9]+", "-", top.lower()).strip("-")


def iter_docs():
    for root, dirs, files in os.walk(wp(SRC)):
        # conversion bookkeeping (.convert_*), backups, and any other dot
        # state must never enter the index
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for fn in files:
            if fn.lower().endswith(".md") and not fn.startswith("."):
                full = os.path.join(root, fn)
                yield os.path.relpath(full, wp(SRC)), full


def main():
    ap = argparse.ArgumentParser(description="Chunk .sources/ into tier 2.")
    ap.add_argument("--rebuild", action="store_true",
                    help="Re-write chunks even when hashes match")
    ap.add_argument("--limit", type=int, help="Process only the first N docs")
    args = ap.parse_args()

    if not SRC.is_dir():
        log(f"ERR: no source corpus at {SRC}")
        return 2

    cp = load_sibling("contextual_prefix", "contextual-prefix.py")
    log(f"chunker: target={cp.CHUNK_TARGET_CHARS}c "
        f"overlap={cp.CHUNK_OVERLAP_CHARS}c")

    docs = sorted(iter_docs())
    if args.limit:
        docs = docs[: args.limit]
    total = len(docs)
    log(f"corpus: {total} documents under .sources/")

    os.makedirs(wp(CHUNKS), exist_ok=True)
    n_chunks = n_written = n_skipped = n_err = n_stale = 0
    t0 = time.time()

    for i, (rel, full) in enumerate(docs, 1):
        try:
            body = io.open(wp(full), encoding="utf-8", errors="replace").read()
        except OSError as exc:
            n_err += 1
            log(f"[{i}/{total}] ERR read {type(exc).__name__} :: {rel}")
            continue
        if not body.strip():
            continue

        # Normalize before chunking so boundaries are computed on clean text.
        body = normalize_filler(body)
        page_body_hash = sha256(body)
        addr = address_for(rel)
        title = strip_suffix(os.path.basename(rel))
        folder = os.path.dirname(rel).replace("\\", "/")
        prefix = corpus_prefix(title, folder, body)

        chunk_dir = os.path.join(str(CHUNKS), addr)
        os.makedirs(wp(chunk_dir), exist_ok=True)

        for idx, raw in enumerate(split_oversized(cp.chunk_body(body))):
            n_chunks += 1
            path = os.path.join(chunk_dir, f"chunk-{idx:03d}.json")
            body_hash = sha256(raw)
            if not args.rebuild and os.path.exists(wp(path)):
                try:
                    prev = json.loads(io.open(wp(path), encoding="utf-8").read())
                    if (prev.get("body_hash") == body_hash and
                            prev.get("page_body_hash") == page_body_hash):
                        n_skipped += 1
                        continue
                except (OSError, ValueError):
                    pass
            record = {
                "schema_version": SCHEMA_VERSION,
                "page_path": os.path.join(".sources", rel),
                "page_address": addr,
                "tier": tier_for(rel),
                "chunk_index": idx,
                "raw_text": raw,
                "contextualized_text": prefix + "\n\n" + raw,
                "prefix": prefix,
                "prefix_source": "synthetic-corpus",
                "char_count": len(raw),
                "body_hash": body_hash,
                "page_body_hash": page_body_hash,
                "created_at": datetime.now(timezone.utc)
                                      .strftime("%Y-%m-%dT%H:%M:%SZ"),
            }
            tmp = path + f".{os.getpid()}.tmp"
            io.open(tmp, "w", encoding="utf-8").write(
                json.dumps(record, ensure_ascii=False, indent=2))
            os.replace(wp(tmp), wp(path))
            n_written += 1

        # LOCAL PATCH (2026-07-28): drop the tail a shrunk document leaves
        # behind. Chunk files are written per index, never swept, so when a
        # document is edited down to fewer chunks the surplus files survive
        # carrying the SUPERSEDED text — and stay indexed and citable.
        # `idx` is the last index written for this document.
        for stale in sorted(glob.glob(os.path.join(chunk_dir, "chunk-*.json"))):
            try:
                n = int(os.path.basename(stale).split("-")[1].split(".")[0])
            except (IndexError, ValueError):
                continue
            if n > idx:
                os.remove(wp(stale))
                n_stale += 1

        if i % 100 == 0 or i == total:
            log(f"--- {i}/{total} docs | {n_chunks} chunks | wrote={n_written} "
                f"skipped={n_skipped} err={n_err} | {time.time() - t0:.0f}s")

    log(f"=== DONE: {total} docs, {n_chunks} chunks (wrote={n_written} "
        f"skipped={n_skipped} stale-removed={n_stale} err={n_err}) "
        f"in {(time.time() - t0) / 60:.1f} min ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
