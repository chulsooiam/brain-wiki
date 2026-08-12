#!/usr/bin/env python3
"""test_contextual_prefix.py — hermetic tests for scripts/contextual-prefix.py.

Covers the Haiku cache-floor decision (cache_control_for). The network paths
(tier-1 Anthropic API, tier-2 claude CLI) are egress-gated and excluded from
hermetic tests by design; only the pure floor logic is exercised here. No
network, no LLM, no ollama. Pure stdlib.

Usage:
  python3 tests/test_contextual_prefix.py
"""
import importlib.util
import json
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
HELPER = ROOT / "scripts" / "contextual-prefix.py"

spec = importlib.util.spec_from_file_location("contextual_prefix", HELPER)
cp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cp)


class Fail(SystemExit):
    pass


def assert_eq(label, expected, actual):
    if expected != actual:
        raise Fail(f"FAIL {label}: expected {expected!r}, got {actual!r}")
    print(f"OK   {label}")


def assert_true(label, cond):
    if not cond:
        raise Fail(f"FAIL {label}")
    print(f"OK   {label}")


# ─── Below the floor → no cache_control (silent no-op avoided) ───────────────
def test_below_floor_returns_none():
    body = "x" * (cp.HAIKU_CACHE_MIN_CHARS - 1)
    assert_eq("body 1 char below floor → None", None, cp.cache_control_for(body))


def test_empty_body_returns_none():
    assert_eq("empty body → None", None, cp.cache_control_for(""))


# ─── At / above the floor → ephemeral cache_control ──────────────────────────
def test_at_floor_returns_ephemeral():
    body = "x" * cp.HAIKU_CACHE_MIN_CHARS
    assert_eq("body exactly at floor → ephemeral",
              {"type": "ephemeral"}, cp.cache_control_for(body))


def test_above_floor_returns_ephemeral():
    body = "x" * (cp.HAIKU_CACHE_MIN_CHARS * 3)
    assert_eq("body well above floor → ephemeral",
              {"type": "ephemeral"}, cp.cache_control_for(body))


# ─── Integration: built payload attaches cache_control only above the floor ──
def test_payload_attaches_cache_control_by_body_size():
    """Mock the network. Assert the API payload attaches cache_control to the
    page block only when the body clears the floor, and the multi-line model
    reply is truncated to one line. No network, no LLM."""
    captured = {}

    class _Resp:
        def __init__(self, d):
            self._d = json.dumps(d).encode()

        def read(self):
            return self._d

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def _fake_urlopen(req, timeout=None):
        captured["body"] = json.loads(req.data.decode())
        return _Resp({
            "content": [{"type": "text", "text": "one situating line.\nIGNORED"}],
            "usage": {"cache_creation_input_tokens": 7, "cache_read_input_tokens": 3},
        })

    with mock.patch.object(cp.urllib.request, "urlopen", _fake_urlopen):
        out = cp.anthropic_api_prefix("KEY", "T", "x" * cp.HAIKU_CACHE_MIN_CHARS, "chunk")
        assert_eq("multi-line reply truncated to one line", "one situating line.", out)
        assert_true("above-floor body attaches cache_control",
                    "cache_control" in captured["body"]["system"][1])
        cp.anthropic_api_prefix("KEY", "T", "tiny", "chunk")
        assert_true("below-floor body omits cache_control",
                    "cache_control" not in captured["body"]["system"][1])


# ─── Chunk size cap (2026-08-12) ────────────────────────────────────────────
# The defect these cover: a Markdown table has no blank line between its rows,
# so the paragraph chunker treated a whole register page as ONE paragraph and
# emitted a 28,246-char chunk. nomic-embed-text 500s above ~5,000, so those
# chunks lost their rerank silently, for months.
def test_markdown_table_is_split():
    row = "| " + " | ".join(["cell text here"] * 8) + " |\n"
    body = "# Page\n\n" + row * 400          # one paragraph, ~50k chars
    chunks = cp.chunk_body(body)
    assert_true("giant Markdown table yields >1 chunk", len(chunks) > 1)
    assert_true("every chunk within MAX_RAW_CHARS",
                all(len(c) <= cp.MAX_RAW_CHARS for c in chunks))


def test_unbroken_run_is_hard_sliced():
    # No sentence punctuation anywhere: the sentence splitter cannot help, so
    # the hard-slice path is the only thing standing between this and a
    # single unembeddable chunk.
    chunks = cp.chunk_body("word " * 4000)
    assert_true("punctuation-free body still capped",
                all(len(c) <= cp.MAX_RAW_CHARS for c in chunks))


def test_small_pages_are_untouched():
    # Regression guard: capping must not re-chunk pages that never overflowed,
    # or every page's body_hash churns on rebuild for no reason.
    body = "\n\n".join(f"Paragraph {i}. It has two sentences." for i in range(20))
    assert_eq("under-cap page identical with and without cap",
              cp.chunk_body(body, max_chars=0), cp.chunk_body(body))


def test_cap_can_be_disabled():
    # Assert on the largest chunk, not the count: chunk_body also emits a
    # trailing overlap-seeded remnant, which is pre-existing behaviour and not
    # what this test is about.
    body = "x" * 9000
    assert_eq("max_chars=0 leaves the oversized chunk whole",
              9000, max(len(c) for c in cp.chunk_body(body, max_chars=0)))
    assert_true("cap on splits the same body",
                all(len(c) <= cp.MAX_RAW_CHARS for c in cp.chunk_body(body)))


def main():
    print("=== test_contextual_prefix.py ===")
    test_below_floor_returns_none()
    test_empty_body_returns_none()
    test_at_floor_returns_ephemeral()
    test_above_floor_returns_ephemeral()
    test_payload_attaches_cache_control_by_body_size()
    test_markdown_table_is_split()
    test_unbroken_run_is_hard_sliced()
    test_small_pages_are_untouched()
    test_cap_can_be_disabled()
    print("\nAll contextual-prefix tests passed.")


if __name__ == "__main__":
    main()
