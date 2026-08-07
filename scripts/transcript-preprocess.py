#!/usr/bin/env python3
"""transcript-preprocess.py — deterministic ASR-transcript preprocessor.

The mechanical front half of the transcript-distill skill
(skills/transcript-distill/SKILL.md). Meeting recorders (Plaud, Otter,
Whisper pipelines, …) emit noisy machine transcripts: hallucinated filler
during silence, anonymous "Speaker N" labels, hundreds of two-second
fragments, and filenames whose date prefix disagrees with the recording
metadata. Everything about that cleanup that does NOT require judgment
lives here, so the LLM distillation step starts from clean, compact,
trustworthy input — and so the cleanup is reproducible and testable.

Expected transcript shape (tolerant — unparseable lines pass through):

    # 05-06 Standardizing the Client Consultation Workflow
    - plaud_id: 000fd69d…          <- any "- key: value" bullets
    - recorded: 2026-05-06 14:02
    - duration: 58 min
    - transcript: raw ASR fallback (transaction)
    ---
    [00:12 - 00:15] Speaker 1: Good morning everyone.
    [00:15 - 00:41] Speaker 2: Shall we start with the question bank?

Timestamps may be MM:SS or H:MM:SS. Segment lines that do not match the
pattern are treated as continuation text of the previous segment.

CLI:
  transcript-preprocess.py stats FILE...          # per-file JSON profile
  transcript-preprocess.py clean FILE [--out PATH] [--drop-flagged]
                                      [--no-merge] [--phrases PATH]
  transcript-preprocess.py check-dates FILE...    # filename-vs-metadata date audit

`stats`  — segments, speakers, duration, script mix, flagged-noise counts.
`clean`  — re-emit the transcript with: consecutive same-speaker segments
           merged (unless --no-merge), hallucination-suspect segments
           marked with a `[flagged: reason]` prefix (or removed with
           --drop-flagged), and exact-duplicate runs collapsed.
           Default output: stdout. Never modifies the input file
           (.raw/ sources are immutable per skills/wiki-ingest/SKILL.md).
`check-dates` — compares a leading date in the filename (MM-DD or
           YYYY-MM-DD, possibly after an id prefix like "<hex>__") against
           the `recorded:` metadata bullet. The metadata wins; mismatches
           are reported so the distilled page records the true date.

Hallucination heuristics (flag, never silently judge):
  phrase     — segment matches a known ASR-hallucination phrase (builtin
               list below; extend with --phrases FILE, one per line)
  repeat     — 3+ consecutive identical segment texts (all but first)
  empty      — empty/whitespace-only segment text

Exit codes:
  0 — success
  1 — a date mismatch was found (check-dates only)
  2 — usage error
  3 — input file missing or unreadable
"""

import argparse
import json
import re
import sys
import unicodedata
from pathlib import Path

# Common Whisper/ASR hallucinations emitted during silence or music, in the
# languages this tooling has been validated against. Substring match,
# case-insensitive. Deliberately conservative: short generic words ("thanks")
# would flag real speech.
BUILTIN_HALLUCINATION_PHRASES = [
    "thank you for watching",
    "thanks for watching",
    "see you in the next video",
    "don't forget to subscribe",
    "please subscribe",
    "subscribe to the channel",
    "subtitles by",
    "subtitled by",
    "www.mooji.org",
    "ご視聴ありがとうございました",
    "チャンネル登録",
    "시청해 주셔서 감사합니다",
    "구독과 좋아요",
    "다음 영상에서 만나요",
    "gracias por ver",
    "no olvides suscribirte",
]

REPEAT_RUN_THRESHOLD = 3  # >= this many identical consecutive texts → repeat flag

TIMESTAMP = r"(?:\d{1,2}:)?\d{1,2}:\d{2}"
# Speaker label is optional: diarized exports emit "[ts - ts] Speaker 1: text",
# undiarized ones emit "[ts - ts] text". A prefix counts as a label only if it
# is short (<=40 chars) and colon-free — prose with an early colon stays text.
SEGMENT_RE = re.compile(
    rf"^\[(?P<start>{TIMESTAMP})\s*-\s*(?P<end>{TIMESTAMP})\]\s*"
    r"(?:(?P<speaker>[^:\n]{1,40}?):(?:\s|$))?(?P<text>.*)$"
)
META_BULLET_RE = re.compile(r"^-\s+(?P<key>[A-Za-z_][\w ]*):\s*(?P<value>.+?)\s*$")
FILENAME_DATE_RE = re.compile(
    r"(?:^|__|_|-|\s)(?P<date>\d{4}-\d{2}-\d{2}|\d{2}-\d{2})(?=[-_.\s])"
)
RECORDED_DATE_RE = re.compile(r"(?P<date>\d{4}-\d{2}-\d{2})")

EXIT_OK = 0
EXIT_MISMATCH = 1
EXIT_USAGE = 2
EXIT_UNREADABLE = 3


def ts_to_seconds(ts):
    """'1:02:03' or '02:03' → seconds."""
    parts = [int(p) for p in ts.split(":")]
    while len(parts) < 3:
        parts.insert(0, 0)
    h, m, s = parts
    return h * 3600 + m * 60 + s


def seconds_to_ts(total):
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


class Segment:
    __slots__ = ("start", "end", "speaker", "text", "flags")

    def __init__(self, start, end, speaker, text):
        self.start = start          # seconds
        self.end = end              # seconds
        self.speaker = (speaker or "").strip()  # "" = undiarized segment
        self.text = text.strip()
        self.flags = []


def parse_transcript(raw):
    """Split a transcript into (title, metadata dict, preamble lines, segments).

    Preamble = lines between the metadata block and the first segment that
    are neither metadata bullets nor the `---` divider (kept verbatim).
    """
    title = None
    metadata = {}
    preamble = []
    segments = []
    in_body = False

    for line in raw.splitlines():
        m = SEGMENT_RE.match(line)
        if m:
            in_body = True
            segments.append(
                Segment(
                    ts_to_seconds(m.group("start")),
                    ts_to_seconds(m.group("end")),
                    m.group("speaker"),
                    m.group("text"),
                )
            )
            continue
        if in_body:
            # Continuation of the previous segment (wrapped line).
            if segments and line.strip():
                segments[-1].text += " " + line.strip()
            continue
        if title is None and line.startswith("# "):
            title = line[2:].strip()
            continue
        mb = META_BULLET_RE.match(line)
        if mb:
            metadata[mb.group("key").strip().lower()] = mb.group("value")
            continue
        if line.strip() == "---":
            continue
        if line.strip():
            preamble.append(line)

    return title, metadata, preamble, segments


def load_phrases(path):
    phrases = list(BUILTIN_HALLUCINATION_PHRASES)
    if path:
        extra = Path(path).read_text(encoding="utf-8").splitlines()
        phrases.extend(p.strip().lower() for p in extra if p.strip())
    return phrases


def flag_segments(segments, phrases):
    """Attach noise flags in place. Returns count of flagged segments."""
    lowered = [s.text.lower() for s in segments]

    for seg, low in zip(segments, lowered):
        if not seg.text:
            seg.flags.append("empty")
            continue
        for phrase in phrases:
            if phrase in low:
                seg.flags.append("phrase")
                break

    # Exact-duplicate runs: flag all but the first occurrence in the run.
    i = 0
    n = len(segments)
    while i < n:
        j = i + 1
        while j < n and lowered[j] == lowered[i] and segments[i].text:
            j += 1
        if j - i >= REPEAT_RUN_THRESHOLD:
            for k in range(i + 1, j):
                if "repeat" not in segments[k].flags:
                    segments[k].flags.append("repeat")
        i = j

    return sum(1 for s in segments if s.flags)


def merge_segments(segments, max_gap=10, max_chars=1500):
    """Merge consecutive same-speaker unflagged segments separated by
    <= max_gap seconds. Flagged segments never merge (they must stay
    individually visible or droppable). max_chars caps the merged text so
    undiarized transcripts — where every segment is "same speaker" — still
    break into readable, timestamp-addressable paragraphs."""
    merged = []
    for seg in segments:
        prev = merged[-1] if merged else None
        if (
            prev is not None
            and not seg.flags
            and not prev.flags
            and seg.speaker == prev.speaker
            and seg.start - prev.end <= max_gap
            and len(prev.text) + len(seg.text) <= max_chars
        ):
            prev.text += " " + seg.text
            prev.end = seg.end
        else:
            merged.append(seg)
    return merged


def script_mix(text):
    """Rough character-script profile: latin / hangul / cjk / other counts."""
    counts = {"latin": 0, "hangul": 0, "cjk": 0, "other": 0}
    for ch in text:
        if not ch.isalpha():
            continue
        name = unicodedata.name(ch, "")
        if "HANGUL" in name:
            counts["hangul"] += 1
        elif "CJK" in name or "HIRAGANA" in name or "KATAKANA" in name:
            counts["cjk"] += 1
        elif "LATIN" in name:
            counts["latin"] += 1
        else:
            counts["other"] += 1
    return counts


SPANISH_MARKERS = {"el", "la", "los", "las", "que", "de", "en", "es", "una", "para", "pero", "como"}


def spanish_ratio(text):
    words = re.findall(r"[a-záéíóúñü]+", text.lower())
    if not words:
        return 0.0
    hits = sum(1 for w in words if w in SPANISH_MARKERS)
    return round(hits / len(words), 3)


def file_stats(path, phrases):
    raw = Path(path).read_text(encoding="utf-8", errors="replace")
    title, metadata, _preamble, segments = parse_transcript(raw)
    flagged = flag_segments(segments, phrases)
    speakers = sorted({s.speaker for s in segments if s.speaker})
    unlabeled = sum(1 for s in segments if not s.speaker)
    body_text = " ".join(s.text for s in segments)
    duration = segments[-1].end if segments else 0
    flag_breakdown = {}
    for s in segments:
        for f in s.flags:
            flag_breakdown[f] = flag_breakdown.get(f, 0) + 1
    return {
        "file": str(path),
        "title": title,
        "metadata": metadata,
        "segments": len(segments),
        "speakers": speakers,
        "unlabeled_segments": unlabeled,
        "duration_seconds": duration,
        "duration": seconds_to_ts(duration),
        "chars": len(body_text),
        "flagged_segments": flagged,
        "flags": flag_breakdown,
        "script_mix": script_mix(body_text),
        "spanish_ratio": spanish_ratio(body_text),
    }


def clean_transcript(path, phrases, drop_flagged=False, merge=True):
    raw = Path(path).read_text(encoding="utf-8", errors="replace")
    title, metadata, preamble, segments = parse_transcript(raw)
    flag_segments(segments, phrases)
    if drop_flagged:
        segments = [s for s in segments if not s.flags]
    if merge:
        segments = merge_segments(segments)

    out = []
    if title:
        out.append(f"# {title}")
    for key, value in metadata.items():
        out.append(f"- {key}: {value}")
    out.extend(preamble)
    if out:
        out.append("---")
    for s in segments:
        prefix = f"[{seconds_to_ts(s.start)} - {seconds_to_ts(s.end)}]"
        flag_note = f" [flagged: {','.join(s.flags)}]" if s.flags else ""
        label = f" {s.speaker}:" if s.speaker else ""
        out.append(f"{prefix}{label}{flag_note} {s.text}")
    return "\n".join(out) + "\n"


def check_dates(paths):
    """Return (reports, mismatch_count). Metadata `recorded:` is ground truth."""
    reports = []
    mismatches = 0
    for path in paths:
        p = Path(path)
        raw = p.read_text(encoding="utf-8", errors="replace")
        _title, metadata, _pre, _segs = parse_transcript(raw)
        recorded = metadata.get("recorded", "")
        rm = RECORDED_DATE_RE.search(recorded)
        fm = FILENAME_DATE_RE.search(p.stem)
        report = {
            "file": str(path),
            "filename_date": fm.group("date") if fm else None,
            "recorded_date": rm.group("date") if rm else None,
            "match": None,
        }
        if report["filename_date"] and report["recorded_date"]:
            fn = report["filename_date"]
            rec = report["recorded_date"]
            # A short MM-DD filename date only has to match the tail.
            report["match"] = rec.endswith(fn) if len(fn) == 5 else fn == rec
            if not report["match"]:
                mismatches += 1
        reports.append(report)
    return reports, mismatches


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    p_stats = sub.add_parser("stats", help="per-file JSON profile")
    p_stats.add_argument("files", nargs="+")
    p_stats.add_argument("--phrases", help="extra hallucination phrases, one per line")

    p_clean = sub.add_parser("clean", help="emit cleaned transcript")
    p_clean.add_argument("file")
    p_clean.add_argument("--out", help="write here instead of stdout")
    p_clean.add_argument("--drop-flagged", action="store_true")
    p_clean.add_argument("--no-merge", action="store_true")
    p_clean.add_argument("--phrases", help="extra hallucination phrases, one per line")

    p_dates = sub.add_parser("check-dates", help="filename vs recorded: date audit")
    p_dates.add_argument("files", nargs="+")

    args = parser.parse_args(argv)

    try:
        if args.command == "stats":
            phrases = load_phrases(args.phrases)
            results = [file_stats(f, phrases) for f in args.files]
            print(json.dumps(results if len(results) > 1 else results[0],
                             indent=2, ensure_ascii=False))
            return EXIT_OK

        if args.command == "clean":
            phrases = load_phrases(args.phrases)
            cleaned = clean_transcript(
                args.file, phrases,
                drop_flagged=args.drop_flagged,
                merge=not args.no_merge,
            )
            if args.out:
                Path(args.out).write_text(cleaned, encoding="utf-8")
            else:
                sys.stdout.write(cleaned)
            return EXIT_OK

        if args.command == "check-dates":
            reports, mismatches = check_dates(args.files)
            print(json.dumps(reports if len(reports) > 1 else reports[0],
                             indent=2, ensure_ascii=False))
            return EXIT_MISMATCH if mismatches else EXIT_OK
    except OSError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_UNREADABLE

    return EXIT_USAGE


if __name__ == "__main__":
    sys.exit(main())
