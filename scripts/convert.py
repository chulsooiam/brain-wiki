#!/usr/bin/env python3
"""Corpus conversion driver: source documents -> Markdown mirror.

Single file (prints markdown to stdout unless -o is given; flags to stderr):

    python convert.py "path/to/doc.pptx" [-o out.md]

Batch (mirrors SRC's tree into DST; every converted file becomes
<name>.<ext>.md, matching the .sources/ naming convention). DST defaults to
the vault's .sources/ so the corpus tier can index the output directly:

    python convert.py --batch SRC [DST] [--workers 4] [--limit N] [--only pdf,docx]

Batch behaviour (proven bookkeeping from large corpus runs):
- resumable: DST/.convert_done.txt records finished relpaths; re-run to resume
- flags:     DST/.convert_flags.jsonl (one JSON record per flagged file) —
             this is the worklist for the finishing layers (OCR/vision,
             numbering review, attachment routing)
- errors:    DST/.convert_errors.txt; errored files are marked done so a
             resume does not stall on them
- log:       DST/.convert.log
- .md/.txt are copied through; xlsx/xls/xlsm/csv are recorded as
  policy-skipped (catalogue, don't convert); unknown extensions are recorded
  and skipped

Dependencies (install in whichever interpreter runs this): docling (PDF/DOCX),
python-pptx (PPTX), markdownify or html2text (HTML/EML html bodies),
extract-msg (MSG only). Run with PYTHONUTF8=1 on Windows — a legacy console
codepage corrupts output otherwise.

After a batch: corpus-retrieve.py auto-builds the corpus index on first
query (default-on hybrid retrieval), so no manual index step is needed.
"""
import argparse
import io
import json
import os
import shutil
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from convert_formats import HANDLERS, PASSTHROUGH, POLICY_SKIP, wp  # noqa: E402

VAULT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_DST = os.path.join(VAULT_ROOT, ".sources")

_lock = threading.Lock()
_state = {"done": 0, "ok": 0, "copied": 0, "skipped": 0, "err": 0,
          "flagged": 0, "total": 0, "start": 0.0, "dst": None}


def _log(msg):
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    with _lock:
        print(line, flush=True)
        if _state["dst"]:
            with io.open(os.path.join(_state["dst"], ".convert.log"), "a",
                         encoding="utf-8") as fh:
                fh.write(line + "\n")


def _append(name, text):
    with _lock:
        with io.open(os.path.join(_state["dst"], name), "a",
                     encoding="utf-8") as fh:
            fh.write(text)


def _ext(path):
    return os.path.splitext(path)[1].lower()


def convert_file(src):
    """Convert one file; returns (markdown, flags). Raises on failure."""
    handler = HANDLERS.get(_ext(src))
    if handler is None:
        raise ValueError(f"no handler for {_ext(src)!r}")
    return handler(src)


# --------------------------------------------------------------------------
# batch mode
# --------------------------------------------------------------------------
def _collect(src_root, only):
    items = []
    for dirpath, _dirs, files in os.walk(wp(src_root)):
        for name in files:
            path = os.path.join(dirpath, name)
            rel = os.path.relpath(path, wp(src_root))
            ext = _ext(name)
            if only and ext.lstrip(".") not in only:
                continue
            items.append((path, rel, ext))
    return sorted(items, key=lambda t: t[1])


def _write_out(out_path, text):
    os.makedirs(wp(os.path.dirname(out_path)), exist_ok=True)
    tmp = out_path + ".tmp"
    with io.open(wp(tmp), "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    os.replace(wp(tmp), wp(out_path))


def _process(path, rel, ext, dst_root, idx):
    total = _state["total"]
    try:
        if ext in PASSTHROUGH:
            out = os.path.join(dst_root, rel)
            os.makedirs(wp(os.path.dirname(out)), exist_ok=True)
            shutil.copy2(wp(path), wp(out))
            with _lock:
                _state["copied"] += 1
            _log(f"[{idx}/{total}] COPY :: {rel}")
        elif ext in POLICY_SKIP:
            with _lock:
                _state["skipped"] += 1
            _append(".convert_skipped.txt", rel + "\texcel-policy: catalogue, don't convert\n")
            _log(f"[{idx}/{total}] SKIP (excel policy) :: {rel}")
        elif ext in HANDLERS:
            text, flags = HANDLERS[ext](path)
            _write_out(os.path.join(dst_root, rel + ".md"), text)
            with _lock:
                _state["ok"] += 1
            if flags:
                with _lock:
                    _state["flagged"] += 1
                _append(".convert_flags.jsonl",
                        json.dumps({"file": rel, "flags": flags},
                                   ensure_ascii=False) + "\n")
            _log(f"[{idx}/{total}] OK ({len(text)}c"
                 + (f", {len(flags)} flag(s)" if flags else "") + f") :: {rel}")
        else:
            with _lock:
                _state["skipped"] += 1
            _append(".convert_skipped.txt", rel + f"\tno handler for {ext}\n")
            _log(f"[{idx}/{total}] SKIP (no handler {ext}) :: {rel}")
        _append(".convert_done.txt", rel + "\n")
    except Exception as exc:  # noqa: BLE001 - keep the batch alive
        with _lock:
            _state["err"] += 1
        _append(".convert_errors.txt",
                rel + "\t" + type(exc).__name__ + ": "
                + str(exc).replace("\n", " ")[:300] + "\n")
        _append(".convert_done.txt", rel + "\n")
        _log(f"[{idx}/{total}] ERROR {type(exc).__name__}: {exc} :: {rel}")

    with _lock:
        _state["done"] += 1
        d = _state["done"]
    if d % 10 == 0:
        rate = (time.time() - _state["start"]) / d
        _log(f"--- progress {d}/{total} | {rate:.1f}s/item | "
             f"ETA {(total - d) * rate / 60:.0f} min | ok={_state['ok']} "
             f"copied={_state['copied']} skipped={_state['skipped']} "
             f"err={_state['err']}")


def run_batch(src_root, dst_root, workers, limit, only):
    os.makedirs(wp(dst_root), exist_ok=True)
    _state["dst"] = dst_root

    done = set()
    done_file = os.path.join(dst_root, ".convert_done.txt")
    if os.path.exists(done_file):
        with io.open(done_file, encoding="utf-8") as fh:
            done = {l.strip() for l in fh if l.strip()}

    items = _collect(src_root, only)
    todo = [t for t in items if t[1] not in done]
    if limit:
        todo = todo[:limit]
    _state["total"] = len(todo)
    _state["start"] = time.time()
    _log(f"=== convert start: {len(items)} total, {len(done)} done, "
         f"{len(todo)} to process, {workers} workers ===")

    idx_map = {rel: i for i, (_, rel, _e) in enumerate(todo, 1)}
    shards = [todo[i::workers] for i in range(workers)]
    threads = [threading.Thread(
        target=lambda s: [_process(p, r, e, dst_root, idx_map[r]) for p, r, e in s],
        args=(shard,)) for shard in shards if shard]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    mins = (time.time() - _state["start"]) / 60
    _log(f"=== DONE: ok={_state['ok']} copied={_state['copied']} "
         f"skipped={_state['skipped']} flagged={_state['flagged']} "
         f"errors={_state['err']} in {mins:.1f} min ===")
    return 1 if _state["err"] else 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("src", help="source file, or source root with --batch")
    ap.add_argument("dst", nargs="?",
                    help="destination root (batch mode; defaults to the "
                         "vault's .sources/)")
    ap.add_argument("-o", "--out", help="output file (single mode)")
    ap.add_argument("--batch", action="store_true")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--only", help="comma-separated extensions, e.g. pdf,docx")
    args = ap.parse_args()

    if args.batch:
        only = {e.strip().lstrip(".").lower()
                for e in args.only.split(",")} if args.only else None
        return run_batch(args.src, args.dst or DEFAULT_DST, args.workers,
                         args.limit, only)

    text, flags = convert_file(args.src)
    if args.out:
        _write_out(args.out, text)
        print(f"wrote {args.out} ({len(text)} chars)")
    else:
        sys.stdout.write(text)
    for f in flags:
        print(f"FLAG [{f['type']}] {f['detail']}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
