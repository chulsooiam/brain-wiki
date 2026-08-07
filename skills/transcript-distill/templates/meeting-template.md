---
type: meeting
title: "{{TITLE}}"
recorded: {{YYYY-MM-DD}}          # from `recorded:` metadata — NEVER the filename date
duration: "{{H:MM:SS}}"
transcript_quality: raw-asr        # raw-asr | polished
source_file: ".raw/transcripts/{{FILENAME}}"
series: "[[{{SERIES-PAGE}}]]"      # optional: recurring-meeting series page
created: {{YYYY-MM-DD}}
tags: [meeting]
---

# {{TITLE}}

> [!note] Distilled from a raw ASR transcript
> Quotes may contain transcription errors. Timestamps link claims back to
> the source: `.raw/transcripts/{{FILENAME}}`.
> (Drop this callout if `transcript_quality: polished`.)

## Summary

Two to five sentences. What was this meeting *for*, and what changed because it happened?

## Participants

| Label | Identity | Evidence |
|---|---|---|
| Speaker 1 | likely {{NAME}} (inferred) | addressed as "{{NAME}}" at {{H:MM:SS}} |
| Speaker 2 | unknown | — |

## Decisions

- **{{Decision}}** — context in one line. `[{{H:MM:SS}}]`
- …

## Action items

- [ ] {{Action}} — owner: {{Speaker N / name}}, due: {{date or "not stated"}} `[{{H:MM:SS}}]`
- …

## Topics discussed

### {{Topic 1}}
Two to four sentences of distillation. Link entities: [[{{Entity}}]].

### {{Topic 2}}
…

## Notable quotes

> "{{Original-language quote.}}" — Speaker N `[{{H:MM:SS}}]`
> ({{Translation, if not in the vault's primary language.}})

## Open questions

- {{Anything raised but not resolved — these seed follow-ups.}}

## Related

- Previous in series: [[{{PREVIOUS-MEETING-PAGE}}]]
- {{Policy/project pages this meeting touched}}
