---
name: transcript-distill
description: "Distill noisy ASR meeting transcripts (Plaud, Otter, Whisper) into structured wiki meeting pages: decisions, action items, topics, inferred speakers. Filters hallucinations deterministically before the LLM pass. Triggers on: distill transcript, distill this meeting, process meeting transcripts, transcript distill, ingest transcripts, meeting notes from transcript, batch distill."
---

# transcript-distill: Meeting Transcript Distillation

Raw ASR transcripts are the worst possible wiki input: 3-hour meetings as 2,000 timestamped fragments, anonymous `Speaker 1/2/3` labels, Whisper hallucinations during silence, and code-switched multilingual speech. Do **not** feed them to `wiki-ingest` as prose. This skill turns each transcript into one compact **meeting page** — decisions, actions, topics — with everything mechanical done deterministically first.

**Division of labor** (the core design decision of this skill):

| stage | who | what |
|---|---|---|
| parse, de-noise, merge, date audit | `scripts/transcript-preprocess.py` | reproducible, testable, no judgment |
| summarize, infer speakers, extract decisions | the LLM (you) | judgment, clearly marked as such |

---

## Transport, mode, and locking

This skill writes wiki pages, so the shared v1.7+ rules apply exactly as in [`skills/wiki-ingest/SKILL.md`](../wiki-ingest/SKILL.md):

- **Transport**: consult `.vault-meta/transport.json`; use the preferred transport.
- **Mode**: route pages via `python3 scripts/wiki-mode.py route source "<title>"`.
- **Locks**: `bash scripts/wiki-lock.sh acquire <path>` before every page write; release after.
- **Addresses**: opt-in DragonScale addresses per the wiki-ingest Address Assignment section.
- **`.raw/` is immutable**: never modify source transcripts. Cleaned output goes to stdout or a new file — never overwrites the original.

---

## Single transcript workflow

1. **Profile** the transcript first — never read the raw file blind:
   ```bash
   python3 scripts/transcript-preprocess.py stats .raw/transcripts/FILE.md
   ```
   The JSON tells you: title, metadata, segment/speaker counts, duration, flagged-noise counts, script mix (latin/hangul/cjk), and a Spanish-likelihood ratio. A 4-hour transcript with 12 flagged segments and `spanish_ratio: 0.4` needs different handling than a 20-minute English standup.

2. **Audit the date.** Filename date prefixes lie (export order ≠ recording date):
   ```bash
   python3 scripts/transcript-preprocess.py check-dates .raw/transcripts/FILE.md
   ```
   Exit 1 means mismatch — **always trust the `recorded:` metadata**, and record the true date in the meeting page frontmatter.

3. **Clean**:
   ```bash
   python3 scripts/transcript-preprocess.py clean .raw/transcripts/FILE.md --out /tmp/cleaned.md
   ```
   Merges consecutive same-speaker fragments into readable turns and marks hallucination-suspect segments `[flagged: phrase|repeat|empty]`. Add `--drop-flagged` only after skimming what got flagged — the filter is conservative by design, but a real sentence can contain a flagged phrase. Domain-specific hallucinations go in a phrases file (`--phrases`), one per line.

4. **Read the cleaned transcript** completely. For very long meetings (>2h), read in order without skimming the middle — decisions cluster at topic boundaries and meeting ends.

> [!note] Undiarized transcripts
> Some exports carry no speaker labels at all (`[00:02 - 00:37] text…`). The preprocessor handles these — `stats` reports them under `unlabeled_segments`, and `clean` breaks the wall of text into timestamp-addressable paragraphs (merge is capped at ~1,500 chars per paragraph). Skip step 5 for these files; the Participants table just lists "undiarized recording".

5. **Infer speaker identities — with evidence only.** The transcript says `Speaker 1`. You may infer a name **only from in-transcript evidence** (self-introduction, being addressed by name, role references). Record inferences in the page's Participants table with the evidence, formatted as `Speaker 1 — likely NAME (inferred: addressed as "NAME" at 00:14:32)`. If there is no evidence, leave the label anonymous. **Never guess from context outside the transcript** (calendar, prior pages) without marking the lower confidence explicitly.

6. **Write the meeting page** using [`templates/meeting-template.md`](templates/meeting-template.md). Route via `wiki-mode.py route source`, acquire the lock, write, release. The page is a *distillation* — decisions, action items, topics, notable quotes with timestamps — not a shortened transcript. Target 60-150 lines regardless of meeting length; link the raw file for anything deeper.

7. **Cross-reference** per wiki-ingest steps 4-8: entity pages for organizations/products/projects discussed, concept pages only for genuinely new ideas, index/hot/log updates. Meetings mention many entities in passing — create or update entity pages only for entities that were *discussed*, not merely named.

8. **Check for contradictions** (wiki-ingest §Contradictions): meetings are where positions change. If a decision recorded here conflicts with an existing wiki page — a policy draft, a previous meeting's decision — add `> [!contradiction]` callouts on **both** pages. A meeting reversing an earlier decision is the single highest-value thing this skill can capture; also update the older page's decision status rather than leaving it stale.

---

## Multilingual transcripts

- Summarize in the vault's primary language; quote **notable quotes in their original language** with a translation underneath.
- Raw-ASR non-English text often carries systematic misspellings (e.g., Spanish via an English-biased decoder: "eniciar", "oficena"). Quote sparingly from such passages and normalize obvious ASR misspellings in *your* summary prose (not in quotes), so retrieval doesn't index garbage terms.
- The `script_mix` and `spanish_ratio` stats tell you what you're dealing with before you read.

## Provenance

Carry the source transcript's provenance into the page frontmatter (`transcript_quality: polished | raw-asr`). Raw-ASR pages deserve a standing caution: quotes may be imperfect. The template includes this.

---

## Batch mode

Trigger: "distill all transcripts", a folder path, or 3+ files.

1. Run `stats` and `check-dates` over everything first (both accept multiple files). Report the corpus shape to the user: count, total duration, date mismatches, heavy-noise files, non-English files. Confirm before proceeding.
2. Respect delta tracking (`.raw/.manifest.json`, wiki-ingest §Delta Tracking) — hash-skip already-distilled transcripts.
3. Process each transcript per the single workflow, **but** defer index/hot/log updates to one pass at the end (wiki-ingest §Batch Ingest).
4. Near-empty transcripts (a few segments, most flagged): skip page creation, list them in the final report instead. A 368-byte truncated recording does not deserve a wiki page.
5. For 30+ transcripts, check in with the user every 10. Parallel sub-agents may be used per [`agents/wiki-ingest.md`](../../agents/wiki-ingest.md) — locks are mandatory.

---

## What not to do

- Do not ingest a raw transcript with `wiki-ingest` as if it were an article. The result is a 40 KB "summary" nobody reads.
- Do not present an inferred speaker name as fact — anywhere, ever. The inference table with evidence is the only place names attach to speaker labels.
- Do not trust filename dates. `check-dates` exists because they were wrong in practice.
- Do not delete flagged segments from `.raw/` sources. Immutability rule.
- Do not quote long verbatim passages from raw-ASR transcripts — distill.

---

## How to think (10-principle mapping)

See [`skills/think/SKILL.md`](../think/SKILL.md) for the canonical framework.

| # | Principle | Application here |
|---|-----------|-------------------|
| 1 | OBSERVE (ext) | Run `stats` before reading. Know the noise level, languages, and duration before forming an impression. |
| 2 | OBSERVE (int) | Am I over-trusting a fluent-looking polished transcript? Fluency is not accuracy. |
| 3 | LISTEN | Why does the user keep these recordings? The decisions and commitments are the payload; the chatter is not. |
| 4 | THINK | Which 5-10 moments in this meeting will matter in six months? Extract those. |
| 5 | CONNECT (lat) | Does this meeting contradict a policy page or an earlier meeting? That is the highest-signal finding. |
| 6 | CONNECT (sys) | preprocess → distill → cross-reference → contradiction pass. Deterministic first, judgment second. |
| 7 | FEEL | An anonymous "Speaker 2 disagreed" that is *true* beats a named attribution that is *guessed*. |
| 8 | ACCEPT | Some transcripts are unrecoverable noise. Skipping them is a valid outcome. |
| 9 | CREATE | One compact meeting page per transcript: decisions, actions, topics, quotes with timestamps. |
| 10 | GROW | Recurring meetings form series — link consecutive pages and track how decisions evolve across them. |
