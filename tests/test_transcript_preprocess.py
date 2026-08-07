#!/usr/bin/env python3
"""test_transcript_preprocess.py — hermetic tests for scripts/transcript-preprocess.py.

Covers: segment/metadata parsing, H:MM:SS timestamps, continuation lines,
hallucination flagging (phrase / repeat / empty), same-speaker merging with
flag barriers, clean round-trip + --drop-flagged, script-mix and Spanish
heuristics, and filename-vs-recorded date auditing. No network, no LLM.

Usage:
  python3 tests/test_transcript_preprocess.py
"""
import importlib.util
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HELPER = ROOT / "scripts" / "transcript-preprocess.py"

spec = importlib.util.spec_from_file_location("transcript_preprocess", HELPER)
tp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tp)


class Fail(SystemExit):
    pass


def assert_eq(label, expected, actual):
    if expected != actual:
        raise Fail(f"FAIL {label}: expected {expected!r}, got {actual!r}")
    print(f"OK   {label}")


def assert_true(label, cond, hint=""):
    if not cond:
        raise Fail(f"FAIL {label}{(': ' + hint) if hint else ''}")
    print(f"OK   {label}")


SAMPLE = """# 05-06 Standardizing the Client Consultation Workflow
- plaud_id: 000fd69d
- recorded: 2026-05-06 14:02
- duration: 58 min
- transcript: raw ASR fallback (transaction)
---
[00:12 - 00:15] Speaker 1: Good morning everyone.
[00:15 - 00:20] Speaker 1: Shall we get started with the agenda?
[00:21 - 00:41] Speaker 2: Yes. First item is the question bank cleanup.
This line continues the previous segment.
[00:42 - 00:44] Speaker 2: Thank you for watching this video.
[1:02:03 - 1:02:05] Speaker 3: Agreed, let's proceed.
[1:02:06 - 1:02:07] Speaker 3: Same text.
[1:02:08 - 1:02:09] Speaker 3: Same text.
[1:02:10 - 1:02:11] Speaker 3: Same text.
[1:02:12 - 1:02:13] Speaker 1:
"""


def test_timestamp_helpers():
    assert_eq("ts_to_seconds MM:SS", 135, tp.ts_to_seconds("02:15"))
    assert_eq("ts_to_seconds H:MM:SS", 3723, tp.ts_to_seconds("1:02:03"))
    assert_eq("seconds_to_ts short", "02:15", tp.seconds_to_ts(135))
    assert_eq("seconds_to_ts long", "1:02:03", tp.seconds_to_ts(3723))


def test_parse():
    title, meta, preamble, segs = tp.parse_transcript(SAMPLE)
    assert_eq("title parsed", "05-06 Standardizing the Client Consultation Workflow", title)
    assert_eq("metadata recorded", "2026-05-06 14:02", meta["recorded"])
    assert_eq("metadata provenance", "raw ASR fallback (transaction)", meta["transcript"])
    assert_eq("segment count", 9, len(segs))
    assert_eq("preamble empty", [], preamble)
    assert_true(
        "continuation line appended",
        segs[2].text.endswith("This line continues the previous segment."),
        segs[2].text,
    )
    assert_eq("H:MM:SS start parsed", 3723, segs[4].start)


def test_flagging():
    _t, _m, _p, segs = tp.parse_transcript(SAMPLE)
    flagged = tp.flag_segments(segs, tp.BUILTIN_HALLUCINATION_PHRASES)
    assert_eq("phrase flag on hallucination", ["phrase"], segs[3].flags)
    assert_eq("first of repeat run unflagged", [], segs[5].flags)
    assert_eq("repeat flags on later duplicates", ["repeat"], segs[6].flags)
    assert_eq("repeat flags on last duplicate", ["repeat"], segs[7].flags)
    assert_eq("empty segment flagged", ["empty"], segs[8].flags)
    assert_eq("total flagged", 4, flagged)


def test_merge():
    _t, _m, _p, segs = tp.parse_transcript(SAMPLE)
    tp.flag_segments(segs, tp.BUILTIN_HALLUCINATION_PHRASES)
    merged = tp.merge_segments(segs)
    # Speaker 1's two openers merge; flagged segments never merge.
    assert_eq("merged count", 7, len(merged))
    assert_true(
        "same-speaker merge concatenates",
        merged[0].text == "Good morning everyone. Shall we get started with the agenda?",
        merged[0].text,
    )
    assert_eq("merge extends end time", 20, merged[0].end)
    flagged_in_merged = [s for s in merged if s.flags]
    assert_eq("flagged segments preserved unmerged", 4, len(flagged_in_merged))


def test_merge_gap_barrier():
    a = tp.Segment(0, 5, "Speaker 1", "One.")
    b = tp.Segment(100, 105, "Speaker 1", "Two, much later.")
    merged = tp.merge_segments([a, b], max_gap=10)
    assert_eq("gap larger than max_gap does not merge", 2, len(merged))


def test_clean_cli():
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "sample.md"
        src.write_text(SAMPLE, encoding="utf-8")

        r = subprocess.run(
            [sys.executable, str(HELPER), "clean", str(src)],
            capture_output=True, text=True,
        )
        assert_eq("clean exit 0", 0, r.returncode)
        assert_true("clean keeps title", r.stdout.startswith("# 05-06 Standardizing"))
        assert_true("clean marks flagged", "[flagged: phrase]" in r.stdout)
        assert_true("clean preserves metadata", "- recorded: 2026-05-06 14:02" in r.stdout)

        r2 = subprocess.run(
            [sys.executable, str(HELPER), "clean", str(src), "--drop-flagged"],
            capture_output=True, text=True,
        )
        assert_true("drop-flagged removes hallucination", "Thank you for watching" not in r2.stdout)
        assert_true("drop-flagged keeps real speech", "question bank cleanup" in r2.stdout)
        assert_true("source file untouched", src.read_text(encoding="utf-8") == SAMPLE)


def test_stats_cli():
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "sample.md"
        src.write_text(SAMPLE, encoding="utf-8")
        r = subprocess.run(
            [sys.executable, str(HELPER), "stats", str(src)],
            capture_output=True, text=True,
        )
        assert_eq("stats exit 0", 0, r.returncode)
        data = json.loads(r.stdout)
        assert_eq("stats segments", 9, data["segments"])
        assert_eq("stats speakers", ["Speaker 1", "Speaker 2", "Speaker 3"], data["speakers"])
        assert_eq("stats flagged", 4, data["flagged_segments"])
        assert_eq("stats flag breakdown", {"phrase": 1, "repeat": 2, "empty": 1}, data["flags"])
        assert_true("stats duration", data["duration_seconds"] > 3700)


def test_script_mix_and_spanish():
    mix = tp.script_mix("hello 안녕하세요 世界")
    assert_true("latin counted", mix["latin"] == 5, str(mix))
    assert_true("hangul counted", mix["hangul"] == 5, str(mix))
    assert_true("cjk counted", mix["cjk"] == 2, str(mix))
    high = tp.spanish_ratio("el problema es que la oficina no tiene los datos para el informe")
    low = tp.spanish_ratio("the office does not have the data for the report")
    assert_true("spanish ratio separates es from en", high > 0.4 > low, f"{high} vs {low}")


UNDIARIZED = """# 02-05 Meeting: Validation Planning
- recorded: 2026-02-05
---
[00:02 - 00:37] So we're going to go over the friction points: data formats, missing rules, and templates.
[00:37 - 01:18] The second bit was the lacking of the implementation of the checker into the workflow.
[01:18 - 01:53] Really tying in what is core into those tools.
"""


def test_undiarized():
    _t, _m, _p, segs = tp.parse_transcript(UNDIARIZED)
    assert_eq("undiarized segment count", 3, len(segs))
    assert_eq("undiarized speaker empty", "", segs[0].speaker)
    assert_true(
        "colon inside prose stays in text",
        segs[0].text.startswith("So we're going to go over the friction points:"),
        segs[0].text,
    )
    tp.flag_segments(segs, tp.BUILTIN_HALLUCINATION_PHRASES)
    merged = tp.merge_segments(segs)
    assert_eq("undiarized segments merge", 1, len(merged))

    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "undiarized.md"
        src.write_text(UNDIARIZED, encoding="utf-8")
        r = subprocess.run(
            [sys.executable, str(HELPER), "stats", str(src)],
            capture_output=True, text=True,
        )
        data = json.loads(r.stdout)
        assert_eq("stats unlabeled count", 3, data["unlabeled_segments"])
        assert_eq("stats speakers empty for undiarized", [], data["speakers"])
        r2 = subprocess.run(
            [sys.executable, str(HELPER), "clean", str(src), "--no-merge"],
            capture_output=True, text=True,
        )
        assert_true(
            "clean omits speaker colon when unlabeled",
            "[00:02 - 00:37] So we're going" in r2.stdout,
            r2.stdout[:200],
        )


def test_merge_char_cap():
    segs = [tp.Segment(i * 10, i * 10 + 5, "Speaker 1", "x" * 900) for i in range(4)]
    merged = tp.merge_segments(segs, max_gap=10, max_chars=1500)
    assert_eq("char cap prevents unbounded merge", 4, len(merged))
    small = [tp.Segment(i * 10, i * 10 + 5, "Speaker 1", "short.") for i in range(4)]
    assert_eq("small segments still merge", 1, len(tp.merge_segments(small)))


def test_check_dates():
    with tempfile.TemporaryDirectory() as tmp:
        # Filename says 03-16, metadata says recorded 2026-04-15 → mismatch.
        bad = Path(tmp) / "abc123__03-16-Weekly-Meeting.md"
        bad.write_text("# T\n- recorded: 2026-04-15 10:00\n---\n[00:01 - 00:02] S1: hi\n",
                       encoding="utf-8")
        good = Path(tmp) / "def456__04-15-Weekly-Meeting.md"
        good.write_text("# T\n- recorded: 2026-04-15 10:00\n---\n[00:01 - 00:02] S1: hi\n",
                        encoding="utf-8")

        reports, mism = tp.check_dates([bad, good])
        assert_eq("mismatch count", 1, mism)
        assert_eq("bad file flagged", False, reports[0]["match"])
        assert_eq("good file passes", True, reports[1]["match"])

        r = subprocess.run(
            [sys.executable, str(HELPER), "check-dates", str(bad)],
            capture_output=True, text=True,
        )
        assert_eq("check-dates exit 1 on mismatch", 1, r.returncode)
        r2 = subprocess.run(
            [sys.executable, str(HELPER), "check-dates", str(good)],
            capture_output=True, text=True,
        )
        assert_eq("check-dates exit 0 on match", 0, r2.returncode)


def main():
    test_timestamp_helpers()
    test_parse()
    test_flagging()
    test_merge()
    test_merge_gap_barrier()
    test_clean_cli()
    test_stats_cli()
    test_script_mix_and_spanish()
    test_undiarized()
    test_merge_char_cap()
    test_check_dates()
    print("\ntest_transcript_preprocess.py: all tests passed.")


if __name__ == "__main__":
    main()
