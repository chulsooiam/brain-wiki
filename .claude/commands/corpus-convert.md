---
description: Convert source documents (PDF/DOCX/PPTX/HTML/EML/MSG) into the Markdown corpus under .sources/ — per-format engines, resumable batch, QA flags.
---

Read the `corpus-convert` skill, then convert what the user asked for.

Usage:
- `/corpus-convert <file>` — convert one document, show the markdown and any QA flags
- `/corpus-convert <folder>` — batch-convert a tree into `.sources/` (resumable)
- `/corpus-convert` with no argument — ask what to convert

Workflow:

1. Single file: `PYTHONUTF8=1 python3 scripts/convert.py "<file>"` — flags
   print to stderr; report them to the user with what each means (the skill's
   per-format table).

2. Batch: `PYTHONUTF8=1 python3 scripts/convert.py --batch "<src>" --workers 4`
   (destination defaults to `.sources/`). Long runs: start detached and poll
   the destination's `.convert.log`. Re-running resumes — errored files are
   recorded in `.convert_errors.txt` and not retried automatically.

3. After a batch, summarize: converted / copied / policy-skipped / errors,
   and the flag counts from `.convert_flags.jsonl` grouped by type. Those
   flags are the finishing-layer worklist (vision pass, numbering audit,
   attachment routing) — offer to work through them, don't silently drop
   them.

4. The corpus index rebuilds itself on the next `/corpus-query` (default-on).
   Mention that; no manual index step.

Privacy: before batch-converting emails (.eml/.msg), ask the user explicitly
whether email content should enter the corpus at all — emails carry
third-party personal content.
