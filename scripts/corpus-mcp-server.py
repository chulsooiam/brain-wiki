#!/usr/bin/env python3
"""MCP server exposing the tier-2 source corpus (2026-07-27).

The `/corpus-query` SKILL only works where Claude Code reads `skills/` — the
CLI and the Claude Code desktop app. The consumer Claude Desktop app has no
skill mechanism; it speaks MCP. This server is the bridge, so the same
converted documents are reachable from ordinary Claude conversations.

Dependency-free on purpose: implements JSON-RPC 2.0 over newline-delimited
stdio directly rather than requiring the `mcp` SDK, which is not installed
here. Nothing to pip install, nothing to break on upgrade.

Two tools:
  search_corpus   hybrid BM25 + cosine rerank; returns document, chunk index,
                  scores and a snippet
  read_document   bounded read of a converted document, so the model can read
                  around a hit instead of answering from a 300-char snippet

read_document is confined to .sources/ — an MCP server is reachable by any
prompt in the app, so it must not become an arbitrary-file-read primitive.

stdout is the protocol channel. All logging goes to stderr; a stray print()
to stdout corrupts the stream and the client drops the connection.

Register in claude_desktop_config.json:
  {"mcpServers": {"vault-corpus": {
     "command": "python",
     "args": ["<vault-path>\\\\scripts\\\\corpus-mcp-server.py"]}}}
"""
import io
import json
import os
import subprocess
import sys
from pathlib import Path

VAULT_ROOT = Path(__file__).resolve().parent.parent
SOURCES = (VAULT_ROOT / ".sources").resolve()
RETRIEVE = VAULT_ROOT / "scripts" / "corpus-retrieve.py"
INDEX = VAULT_ROOT / ".vault-meta" / "corpus" / "bm25" / "index.json"

PROTOCOL_VERSION = "2024-11-05"
SERVER_INFO = {"name": "vault-corpus", "version": "1.0.0"}
MAX_READ_CHARS = 20000

TOOLS = [
    {
        "name": "search_corpus",
        "description": (
            "Search the vault's primary source documents (the converted "
            "corpus under .sources/) using hybrid BM25 + cosine rerank. "
            "Use this to find what an ORIGINAL document actually says, as "
            "opposed to what the curated wiki concluded. Recall is lexical: "
            "prefer the vocabulary the documents themselves use (e.g. "
            "a policy's instruction number, a term of art, a form name). "
            "Returns document paths and chunk indexes - follow up with "
            "read_document to read the surrounding text before answering."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string",
                          "description": "Natural-language or keyword query"},
                "top_k": {"type": "integer", "default": 5,
                          "description": "Results to return (1-20)"},
                "bm25_top": {"type": "integer", "default": 20,
                             "description": "Candidates considered before "
                                            "rerank. Raise to 60 if a known "
                                            "document is not surfacing."},
                "no_rerank": {"type": "boolean", "default": False,
                              "description": "Skip the rerank stage (faster; "
                                             "returns BM25 order)"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "read_document",
        "description": (
            "Read a converted source document returned by search_corpus. "
            "Snippets are locators, not answers - read the document before "
            "citing it. IMPORTANT: clause numbers in these converted files "
            "are frequently FABRICATED by the converter and must never be "
            "cited as the original's; quote the text and cite the document. "
            "Footnotes were often dropped, and '<!-- image -->' marks content "
            "(sometimes whole data tables) that exists only in the original."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string",
                         "description": "page_path or absolute_path from a "
                                        "search_corpus result"},
                "offset": {"type": "integer", "default": 0,
                           "description": "Character offset to start at"},
                "length": {"type": "integer", "default": MAX_READ_CHARS,
                           "description": f"Chars to read (max {MAX_READ_CHARS})"},
            },
            "required": ["path"],
        },
    },
]


def log(msg):
    print(msg, file=sys.stderr, flush=True)


def wp(p):
    ap = os.path.abspath(str(p))
    if os.name == "nt" and not ap.startswith("\\\\?\\"):
        return "\\\\?\\" + ap
    return ap


def run_search(args):
    if not INDEX.is_file():
        return ("Corpus index not built. Run:\n"
                "  python3 scripts/corpus-index.py\n"
                "  python3 scripts/corpus-dedup.py --apply\n"
                "  python3 scripts/corpus-bm25.py build")
    top = max(1, min(int(args.get("top_k", 5) or 5), 20))
    cmd = [sys.executable, str(RETRIEVE), str(args.get("query", "")),
           "--top", str(top),
           "--bm25-top", str(max(top, int(args.get("bm25_top", 20) or 20))),
           "--json"]
    if args.get("no_rerank"):
        cmd.append("--no-rerank")
    env = dict(os.environ, PYTHONUTF8="1")
    try:
        r = subprocess.run(cmd, capture_output=True, text=True,
                           encoding="utf-8", errors="replace",
                           timeout=180, env=env, cwd=str(VAULT_ROOT))
    except subprocess.TimeoutExpired:
        return "Search timed out after 180s."
    if r.returncode == 10:
        return "Corpus index not provisioned. " + (r.stderr or "").strip()
    if r.returncode != 0:
        return f"Search failed (exit {r.returncode}): {(r.stderr or '').strip()[:500]}"
    try:
        data = json.loads(r.stdout)
    except ValueError:
        return f"Could not parse search output: {r.stdout[:500]}"

    lines = [f"{len(data.get('candidates', []))} results "
             f"[{data.get('strategy', '?')}] for: {data.get('query', '')}", ""]
    for c in data.get("candidates", []):
        rs = c.get("rerank_score")
        score = f"rerank {rs:.3f}" if isinstance(rs, float) else "bm25 order"
        lines.append(f"- {c.get('page_path')}")
        lines.append(f"  chunk {c.get('chunk_index')} | {score} | "
                     f"bm25 {c.get('bm25_score', 0):.1f}")
        lines.append(f"  {c.get('snippet', '')[:300]}")
        lines.append("")
    return "\n".join(lines)


def run_read(args):
    raw = (args.get("path") or "").strip().strip('"')
    if not raw:
        return "No path given."
    p = Path(raw)
    if not p.is_absolute():
        p = VAULT_ROOT / raw
    try:
        resolved = p.resolve()
    except OSError:
        return f"Cannot resolve path: {raw}"
    # Confine to .sources/: this server is callable by any prompt, so it must
    # not become a general file-read tool.
    try:
        resolved.relative_to(SOURCES)
    except ValueError:
        return (f"Refused: {resolved} is outside {SOURCES}. This tool only "
                "reads converted source documents.")
    if not os.path.isfile(wp(resolved)):
        return f"Not found: {resolved}"

    offset = max(0, int(args.get("offset", 0) or 0))
    length = max(1, min(int(args.get("length", MAX_READ_CHARS)
                            or MAX_READ_CHARS), MAX_READ_CHARS))
    try:
        text = io.open(wp(resolved), encoding="utf-8", errors="replace").read()
    except OSError as exc:
        return f"Read failed: {exc}"
    total = len(text)
    body = text[offset:offset + length]
    header = f"{resolved.name} — chars {offset}..{offset + len(body)} of {total}"
    if offset + len(body) < total:
        header += f" (more follows; call again with offset={offset + len(body)})"
    return header + "\n\n" + body


def handle(msg):
    """Return a response dict, or None for notifications."""
    method = msg.get("method")
    mid = msg.get("id")

    if method == "initialize":
        want = (msg.get("params") or {}).get("protocolVersion")
        return {"jsonrpc": "2.0", "id": mid, "result": {
            "protocolVersion": want or PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": SERVER_INFO}}

    if method in ("notifications/initialized", "initialized"):
        return None

    if method == "ping":
        return {"jsonrpc": "2.0", "id": mid, "result": {}}

    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": mid, "result": {"tools": TOOLS}}

    if method == "tools/call":
        params = msg.get("params") or {}
        name = params.get("name")
        args = params.get("arguments") or {}
        try:
            if name == "search_corpus":
                text = run_search(args)
            elif name == "read_document":
                text = run_read(args)
            else:
                return {"jsonrpc": "2.0", "id": mid, "error": {
                    "code": -32601, "message": f"Unknown tool: {name}"}}
        except Exception as exc:                       # noqa: BLE001
            log(f"tool {name} raised: {exc!r}")
            return {"jsonrpc": "2.0", "id": mid, "result": {
                "content": [{"type": "text", "text": f"Tool error: {exc}"}],
                "isError": True}}
        return {"jsonrpc": "2.0", "id": mid, "result": {
            "content": [{"type": "text", "text": text}]}}

    if mid is None:
        return None
    return {"jsonrpc": "2.0", "id": mid, "error": {
        "code": -32601, "message": f"Method not found: {method}"}}


def main():
    log(f"vault-corpus MCP server up; vault={VAULT_ROOT}")
    # Binary stdio so the framing is ours, not the platform's newline policy.
    inp = io.TextIOWrapper(sys.stdin.buffer, encoding="utf-8", errors="replace")
    out = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", newline="\n")
    for line in inp:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            log(f"dropping non-JSON line: {line[:120]}")
            continue
        try:
            resp = handle(msg)
        except Exception as exc:                       # noqa: BLE001
            log(f"handler crashed: {exc!r}")
            resp = {"jsonrpc": "2.0", "id": msg.get("id"), "error": {
                "code": -32603, "message": str(exc)}}
        if resp is not None:
            out.write(json.dumps(resp, ensure_ascii=False) + "\n")
            out.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
