---
name: wiki-ingest
description: "Ingest sources into the Obsidian wiki vault. Reads a source, extracts entities and concepts, creates or updates wiki pages, cross-references, and logs the operation. Supports files, URLs, and batch mode. Triggers on: ingest, process this source, add this to the wiki, read and file this, batch ingest, ingest all of these, ingest this url."
---

# wiki-ingest: Source Ingestion

Read the source. Write the wiki. Cross-reference everything. A single source typically touches 8-15 wiki pages.

**Syntax standard**: Write all Obsidian Markdown using proper Obsidian Flavored Markdown. Wikilinks as `[[Note Name]]`, callouts as `> [!type] Title`, embeds as `![[file]]`, properties as YAML frontmatter. If the kepano/obsidian-skills plugin is installed, prefer its canonical obsidian-markdown skill for Obsidian syntax reference. Otherwise, follow the guidance in this skill.

---

## Transport (v1.7+)

Before mutating any vault file, consult `.vault-meta/transport.json` (auto-created by `bash scripts/detect-transport.sh`). Use the `preferred` transport per the fallback chain:

- **cli** — `obsidian-cli write "$VAULT" "$NOTE" < content.md` (or `append`, `property:set`); see [`skills/wiki-cli/SKILL.md`](../wiki-cli/SKILL.md)
- **mcp-obsidian** / **mcpvault** — `mcp__obsidian-vault__write_note` and friends; see [`skills/wiki/references/mcp-setup.md`](../wiki/references/mcp-setup.md)
- **filesystem** — Claude's `Write`/`Edit` tools with absolute vault-rooted paths (final floor; always works)

Full decision tree: [`wiki/references/transport-fallback.md`](../../wiki/references/transport-fallback.md).

---

## Mode awareness (v1.8+)

Before creating any new wiki page, consult the vault's methodology mode via `python3 scripts/wiki-mode.py route <type> "<name>"`. The router returns the vault-relative path where the page should be filed.

```bash
SRC_PATH=$(python3 scripts/wiki-mode.py route source "Karpathy 2025 LLM Wiki essay")
# generic:      wiki/sources/Karpathy-2025-LLM-Wiki-essay.md
# lyt:          wiki/notes/Karpathy-2025-LLM-Wiki-essay.md  (also update relevant MOC)
# para:         wiki/resources/incoming/Karpathy-2025-LLM-Wiki-essay.md
# zettelkasten: wiki/20260517123456-Karpathy-2025-LLM-Wiki-essay.md

ENT_PATH=$(python3 scripts/wiki-mode.py route entity "Andrej Karpathy")
CON_PATH=$(python3 scripts/wiki-mode.py route concept "Compounding Vault Pattern")
```

If `.vault-meta/mode.json` is absent, the router returns mode=generic paths (identical to v1.7 behavior). No special-casing needed in this skill.

Mode-specific follow-up:
- **LYT**: after filing the atomic note, update the relevant MOC (`wiki/mocs/<topic>-moc.md`) to link the new note. If no MOC exists for the topic, create one using `skills/wiki-mode/templates/lyt/moc-template.md`.
- **Zettelkasten**: filename already includes the timestamp ID. Populate the `id:` frontmatter field to match.
- **PARA**: new ingests land in `wiki/resources/incoming/` by default. Do NOT auto-guess the topic; leave in incoming/ for user review.

## Concurrency (v1.7+)

**Multi-writer is safe in v1.7.** The latent corruption bug from v1.6 — where two parallel sub-agents writing to the same page could silently trample each other — is closed by per-file advisory locking. Every wiki page write MUST be preceded by `wiki-lock acquire <path>`.

```bash
# Acquire — blocks (returns 75 EX_TEMPFAIL) if another writer holds the lock
if bash scripts/wiki-lock.sh acquire wiki/concepts/Foo.md; then
  # ... do the write via the §Transport-selected method ...
  bash scripts/wiki-lock.sh release wiki/concepts/Foo.md
else
  # rc=75: another writer is in flight. Retry once after 2s; if still held,
  # log to wiki/log.md and skip this page rather than overwrite.
  sleep 2
  bash scripts/wiki-lock.sh acquire wiki/concepts/Foo.md && {
    # write …
    bash scripts/wiki-lock.sh release wiki/concepts/Foo.md
  } || echo "skipped wiki/concepts/Foo.md (locked); logged to wiki/log.md"
fi
```

Properties:
- **Per-file granularity.** Locks key on `sha1(<vault-relative-path>)`; concurrent writes to DIFFERENT pages run in parallel.
- **Age-based staleness.** Default `STALE_AFTER_SEC=60`. A crashed holder unblocks in ≤60 seconds without manual intervention. See `scripts/wiki-lock.sh` header for the full semantics.
- **Cross-process release.** Release is `rm -f` (no PID match required). Skill authors are trusted to release locks they acquire; cross-skill release is allowed by design (a janitor running `wiki-lock clear-stale --max-age 0` is the canonical recovery path).
- **The PostToolUse hook now defers `git add` if any locks are currently held**, so the auto-commit doesn't fire mid-ingest and produce torn commits. See `hooks/hooks.json`.

`wiki-lock` is unconditional in v1.7+ — there is no feature gate, no fallback. Skills that don't acquire locks are racing against any other writer. The script is in core, not opt-in.

Sub-agent rule from v1.6 — *"Sub-agents MUST NOT call `scripts/allocate-address.sh`"* — is preserved (orchestrator still backfills addresses to keep the counter monotonic). The NEW rule is: *sub-agents MAY now write pages, but MUST acquire locks first.* See `agents/wiki-ingest.md`.

---

## Delta Tracking

Before ingesting any file, check `.raw/.manifest.json` to avoid re-processing unchanged sources.

```bash
# Check if manifest exists
[ -f .raw/.manifest.json ] && echo "exists" || echo "no manifest yet"
```

**Manifest format** (create if missing):
```json
{
  "sources": {
    ".raw/articles/article-slug-2026-04-08.md": {
      "hash": "abc123",
      "ingested_at": "2026-04-08",
      "pages_created": ["wiki/sources/article-slug.md", "wiki/entities/Person.md"],
      "pages_updated": ["wiki/index.md"]
    }
  }
}
```

**Before ingesting a file:**
1. Compute a hash: `md5sum [file] | cut -d' ' -f1` (or `sha256sum` on Linux).
2. Check if the path exists in `.manifest.json` with the same hash.
3. If hash matches, skip. Report: "Already ingested (unchanged). Use `force` to re-ingest."
4. If missing or hash differs, proceed with ingest.

**After ingesting a file:**
1. Record `{hash, ingested_at, pages_created, pages_updated}` in `.manifest.json`.
2. Write the updated manifest back.

Skip delta checking if the user says "force ingest" or "re-ingest".

---

## Ingestion Depth (tiered corpora)

**Opt-in, feature-detected.** If `.raw/.ingest-plan.json` exists, consult it before processing any file; if it is absent, every file gets `full` treatment — identical to pre-plan behavior.

Institutional corpora mix 3 KB memos with 850-page reference books and 5,000-row spreadsheets. One-size ingestion drowns signal in bulk. The plan (managed by `scripts/ingest-depth.py`) assigns each source one of four depths:

```bash
DEPTH=$(python3 scripts/ingest-depth.py get .raw/documents/FILE.pdf)   # full|summary|catalogue|skip
```

| depth | treatment |
|---|---|
| `full` | The complete Single Source Ingest flow above. The default. |
| `summary` | ONE source page: structured summary + a section/chapter index (so retrieval can point into the original), entity pages only for principal actors. Do **not** enumerate every claim; 100-200 lines total. Frontmatter: `ingest_depth: summary`. |
| `catalogue` | NO prose ingestion. A registry entry only — for spreadsheets and survey forms, hand off to [`skills/form-catalogue/SKILL.md`](../form-catalogue/SKILL.md); for other files, one line in the relevant `_index.md` with path, size, and a one-sentence description. |
| `skip` | Not wiki material (OS junk, empty stubs). Mention in the batch report, nothing else. |

Plan lifecycle (operator-facing):

```bash
python3 scripts/ingest-depth.py assign .raw/documents/ --dry-run   # propose + review
python3 scripts/ingest-depth.py assign .raw/documents/             # write plan
python3 scripts/ingest-depth.py set .raw/documents/big-report.pdf full --reason "core policy text"
python3 scripts/ingest-depth.py summary                            # counts per depth
```

Heuristics are deterministic (extension class + size; see the script header). Operator overrides carry `"override": true` and survive re-assignment. During batch ingest, report the depth mix before starting so the user can adjust the plan first.

---

## Inbox

`.raw/_inbox.md` is a drop file for sources encountered mid-workday — a URL pasted from a phone, a file path noted during a meeting — so capture never waits for an ingest session. One item per line, optional note after `|`:

```markdown
https://example.org/new-data-standard | mentioned in Tuesday's WG call
C:/Users/me/Downloads/policy-draft-v3.docx
```

At the start of every batch ingest (and when the user says "drain the inbox"): read `_inbox.md`, process each line via URL Ingestion or Single Source Ingest, then move the line under a `## Processed (YYYY-MM-DD)` heading in the same file with a wikilink to the resulting page. Unreachable URLs and missing paths also move to Processed, marked `FAILED: reason` — the inbox itself always drains to empty. `_inbox.md` joins `.manifest.json` as skill-maintained files under `.raw/` (exceptions to the immutability rule).

---

## URL Ingestion

Trigger: user passes a URL starting with `https://`.

Steps:

1. **Fetch** the page using WebFetch.
2. **Clean** (optional): if `defuddle` is available (`which defuddle 2>/dev/null`), run `defuddle [url]` to strip ads, nav, and clutter. Typically saves 40-60% tokens. Fall back to raw WebFetch output if not installed.
3. **Derive slug** from the URL path (last segment, lowercased, spaces→hyphens, strip query strings).
4. **Save** to `.raw/articles/[slug]-[YYYY-MM-DD].md` with a frontmatter header:
   ```markdown
   ---
   source_url: [url]
   fetched: [YYYY-MM-DD]
   ---
   ```
5. Proceed with **Single Source Ingest** starting at step 2 (file is now in `.raw/`).

---

## Image / Vision Ingestion

Trigger: user passes an image file path (`.png`, `.jpg`, `.jpeg`, `.gif`, `.webp`, `.svg`, `.avif`).

Steps:

1. **Read** the image file using the Read tool. Claude can process images natively.
2. **Describe** the image contents: extract all text (OCR), identify key concepts, entities, diagrams, and data visible in the image.
3. **Save** the description to `.raw/images/[slug]-[YYYY-MM-DD].md`:
   ```markdown
   ---
   source_type: image
   original_file: [original path]
   fetched: YYYY-MM-DD
   ---
   # Image: [slug]

   [Full description of image contents, transcribed text, entities visible, etc.]
   ```
4. Copy the image to `_attachments/images/[slug].[ext]` if it's not already in the vault.
5. Proceed with **Single Source Ingest** on the saved description file.

Use cases: whiteboard photos, screenshots, diagrams, infographics, document scans.

---

## Work Queue (reground loop)

At the start of any ingest session, check the maintenance queue:

```bash
python3 scripts/work-queue.py stats     # quick shape
python3 scripts/work-queue.py list      # the actual items
```

If open items touch pages this session will write anyway, fix them in the same pass. If the queue has accumulated (5+ items), tell the user and propose draining it before taking on new material — stale pages re-entering the pipeline as input is what keeps the wiki trustworthy. Mark items off as you fix them:

```bash
python3 scripts/work-queue.py done --page "Page Name" --kind stale
```

Lint fills this queue (wiki-lint §Lint Checks); anyone can queue items by hand with `add --kind manual`.

---

## Single Source Ingest

Trigger: user drops a file into `.raw/` or pastes content.

Steps:

1. **Read** the source completely. Do not skim.
2. **Discuss** key takeaways with the user. Ask: "What should I emphasize? How granular?" Skip this if the user says "just ingest it."
3. **Create** source summary in `wiki/sources/`. Use the source frontmatter schema from `references/frontmatter.md`. Assign an address per the **Address Assignment** section below.
4. **Create or update** entity pages for every person, org, product, and repo mentioned. One page per entity. Assign addresses to new entity pages.
5. **Create or update** concept pages for significant ideas and frameworks. Assign addresses to new concept pages.
6. **Update** relevant domain page(s) and their `_index.md` sub-indexes.
7. **Update** `wiki/overview.md` if the big picture changed.
8. **Update** `wiki/index.md`. Add entries for all new pages.
9. **Update** `wiki/hot.md` with this ingest's context.
10. **Append** to `wiki/log.md` (new entries at the TOP):
    ```markdown
    ## [YYYY-MM-DD] ingest | Source Title
    - Source: `.raw/articles/filename.md`
    - Summary: [[Source Title]]
    - Pages created: [[Page 1]], [[Page 2]]
    - Pages updated: [[Page 3]], [[Page 4]]
    - Key insight: One sentence on what is new.
    ```
11. **Check for contradictions.** If new info conflicts with existing pages, add `> [!contradiction]` callouts on both pages.
12. **Check for supersession.** If this source is a new revision/edition of a document already in the wiki (`REV`, `v2`, `LEGACY_`, draft numbers, "2nd Edition" are the usual tells), wire the lineage: `supersedes: "[[Old Page]]"` on the new page, `superseded_by: "[[New Page]]"` on the old one, and a `> [!stale] Superseded by [[New Page]]` callout at the top of the old page. Both edits, same ingest — asymmetric lineage is a lint finding (`scripts/lineage-check.py`, wiki-lint §Version Lineage). Supersession and contradiction are different relationships: a revision *replacing* a policy is lineage even when nothing conflicts.

---

## Batch Ingest

Trigger: user drops multiple files or says "ingest all of these."

Steps:

1. List all files to process. Confirm with user before starting.
2. Process each source following the single ingest flow. Defer cross-referencing between sources until step 3.
3. After all sources: do a cross-reference pass. Look for connections between the newly ingested sources.
4. Update index, hot cache, and log once at the end (not per-source).
5. **Verifier close-out** (batches of 10+ pages): before the final report, dispatch the verifier agent ([`agents/verifier.md`](../../agents/verifier.md)) over the newly created/updated pages. The agent that wrote a page never reviews it — the verifier is a separate context reading with fresh eyes. Fix BLOCKER/HIGH findings in this session; log MEDIUM/LOW to the defect log (wiki-lint §Defect Log & Meta Loop) and the work queue.
6. Report: "Processed N sources. Created X pages, updated Y pages. Here are the key connections I found." Include the verifier's finding counts when it ran.

Batch ingest is less interactive. For 30+ sources, expect significant processing time. Check in with the user after every 10 sources.

---

## Context Window Discipline

Token budget matters. Follow these rules during ingest:

- Read `wiki/hot.md` first. If it contains the relevant context, don't re-read full pages.
- Read `wiki/index.md` to find existing pages before creating new ones.
- Read only 3-5 existing pages per ingest. If you need 10+, you are reading too broadly.
- Use PATCH for surgical edits. Never re-read an entire file just to update one field.
- Keep wiki pages short. 100-300 lines max. If a page grows beyond 300 lines, split it.
- Use search (`/search/simple/`) to find specific content without reading full pages.

---

## Contradictions

> [!note] Custom callout dependency
> The `[!contradiction]` callout type used below is a **custom callout** defined in `.obsidian/snippets/vault-colors.css` (auto-installed by `/wiki` scaffold). It renders with reddish-brown styling and an alert-triangle icon when the snippet is enabled. If the snippet is missing, Obsidian falls back to default callout styling, so the page still works without the visual flourish. See [[skills/wiki/references/css-snippets.md]] for the four custom callouts (`contradiction`, `gap`, `key-insight`, `stale`).

When new info contradicts an existing wiki page:

On the existing page, add:
```markdown
> [!contradiction] Conflict with [[New Source]]
> [[Existing Page]] claims X. [[New Source]] says Y.
> Needs resolution. Check dates, context, and primary sources.
```

On the new source summary, reference it:
```markdown
> [!contradiction] Contradicts [[Existing Page]]
> This source says Y, but existing wiki says X. See [[Existing Page]] for details.
```

Do not silently overwrite old claims. Flag and let the user decide.

**Register every contradiction** in `wiki/meta/contradictions.md` (create on first use) so open conflicts are queryable in one place instead of scattered across callouts:

```markdown
| # | pages | claim vs claim | found | status |
|---|---|---|---|---|
| 3 | [[Policy Rev1]] ↔ [[Weekly Meeting 2026-05-12]] | policy requires X ↔ meeting agreed to drop X | 2026-05-14 | open |
```

`status` is `open` or `resolved (how)`. When a contradiction is resolved — a revision published, a decision reversed, a source corrected — update the row *and* remove or annotate the callouts on both pages. wiki-lint audits this register (wiki-lint §Lint Checks, check 12).

---

## What Not to Do

- **Source files under `.raw/` are immutable.** Do not modify the files that users drop there (articles, transcripts, images). The `.raw/.manifest.json` delta tracker and its `address_map` (DragonScale Mechanism 2) are the only files under `.raw/` that `wiki-ingest` itself maintains. Treat every other file under `.raw/` as read-only source content.
- Do not create duplicate pages. Always check the index and search before creating.
- Do not skip the log entry. Every ingest must be recorded.
- Do not skip the hot cache update. It is what keeps future sessions fast.

---

## Address Assignment (DragonScale Mechanism 2 MVP)

**Opt-in feature**. DragonScale address assignment runs only if `scripts/allocate-address.sh` is present AND `.vault-meta/` exists. Otherwise, skip this entire section and proceed with ingest normally.

**Feature detection (run at start of every ingest)**:

```bash
if [ -x ./scripts/allocate-address.sh ] && [ -d ./.vault-meta ]; then
  DRAGONSCALE_ADDRESSES=1
else
  DRAGONSCALE_ADDRESSES=0
fi
```

When `DRAGONSCALE_ADDRESSES=0`, pages are created without an `address:` frontmatter field, and `wiki-lint`'s Address Validation section is skipped entirely (missing addresses are not flagged in any severity). This preserves default plugin behavior for vaults that have not adopted DragonScale.

When `DRAGONSCALE_ADDRESSES=1`, proceed with the rest of this section.

---

Every **newly created non-meta wiki page** gets a stable address in its frontmatter:

```yaml
address: c-000042
```

Format: `c-<6-digit-counter>`. The `c-` prefix stands for "creation-order counter." Zero-padded.

Rollout baseline: **2026-04-23** (Phase 2 ship date). Pages with `created:` >= this date are post-rollout and MUST have an address (unless excluded below). Pages with `created:` earlier are legacy-exempt until a deliberate backfill pass assigns `l-NNNNNN` addresses.

### Required tool: `scripts/allocate-address.sh`

Address allocation is delegated to an atomic Bash helper. The helper uses `flock` on `.vault-meta/.address.lock` to prevent read-use-increment races and recovers the counter by scanning existing frontmatter if the counter file is missing.

```bash
ADDR=$(./scripts/allocate-address.sh)
# ADDR is now e.g. "c-000042"; counter is already incremented
```

**CRITICAL**: never use the Write or Edit tool on `.vault-meta/address-counter.txt`. That would fire the PostToolUse hook, which runs `git add wiki/ .raw/` and can accidentally commit unrelated pending wiki changes under a generic message. Counter mutation is **only** permitted through the helper script (Bash tool).

### Helper modes

- `./scripts/allocate-address.sh` — atomically reserves and returns the next address.
- `./scripts/allocate-address.sh --peek` — prints the next value without reserving (safe, read-only).
- `./scripts/allocate-address.sh --rebuild` — recomputes the counter from the highest observed `c-NNNNNN` in existing frontmatter. Never resets to 1 silently if pages already have addresses. Run this if the counter file is suspected corrupt.

### Assignment procedure (per new page)

1. Before writing a new non-meta page, call `./scripts/allocate-address.sh` and capture the output.
2. Include `address: c-XXXXXX` in the page's frontmatter.
3. Record the path-to-address mapping in `.raw/.manifest.json` under a new top-level key `address_map` (see schema below).

### `address_map` in `.raw/.manifest.json`

```json
{
  "sources": { ... },
  "address_map": {
    "wiki/concepts/Example.md": "c-000042",
    "wiki/entities/Another.md": "c-000043"
  }
}
```

On re-ingest of the same source (whether by `--force` or a changed hash), always consult `address_map` first. If the target page path has a prior address, REUSE it. Do not allocate a new one.

On a page rename, the skill must update the `address_map` key (old path -> new path) while preserving the address value.

### Exclusions (do NOT assign an address to)

- Meta files: `_index.md`, `index.md`, `log.md`, `hot.md`, `overview.md`, `dashboard.md`, `dashboard.base`, `Wiki Map.md`, `getting-started.md`.
- Fold pages under `wiki/folds/` (they use their own deterministic `fold_id`).
- Pre-rollout legacy pages (`created:` < 2026-04-23). Legacy pages get `l-NNNNNN` addresses only via a deliberate backfill operation.

### Idempotency rules

- If a page being (re)written already has an `address:` field in its current content, REUSE it. Do not allocate a new one.
- If a source is re-ingested and `address_map` has a mapping for the target path, reuse that mapping.
- If the source has been ingested before AND the target page has no address AND the page `created:` date is post-rollout, allocate an address and record it. This covers the case where an older ingest produced a page before Phase 2 rollout; the rollout cutoff still applies (pages dated pre-2026-04-23 stay legacy).

### Concurrency policy

- **Single-writer only** in Phase 2. Do not run parallel ingests from multiple Claude sessions or sub-agents that assign addresses. The `flock` in the helper prevents counter corruption but does not serialize page writes themselves.
- Sub-agents (codex, general-purpose) that are dispatched for research or review MUST NOT call the allocator. They are read-only in this respect.
- Multi-writer support is a deferred feature.

### Batch ingest

Assign addresses sequentially during single-source-ingest for each source. Do not pre-reserve a block of counter values. The helper is cheap (one lock, one integer read/write).

---

## How to think (10-principle mapping)

When working on this skill, apply the 10-principle loop. See [`skills/think/SKILL.md`](../think/SKILL.md) for the canonical framework.

| # | Principle | Application here |
|---|-----------|-------------------|
| 1 | OBSERVE (ext) | Read the source file completely before extracting anything. No shortcuts on long sources. |
| 2 | OBSERVE (int) | Am I biased toward the source's framing? Where do my disagreements live? Note them as contradiction callouts. |
| 3 | LISTEN | The user's source-selection intent — what made THIS source worth ingesting, and what is the user hoping to extract? |
| 4 | THINK | Which entities deserve pages? Which concepts? What cross-references? What contradictions with existing pages? |
| 5 | CONNECT (lat) | This source's claims vs other sources already in the wiki. Contradictions are the highest-signal finding. |
| 6 | CONNECT (sys) | `wiki-mode.py route` for paths + `wiki-lock.sh` for safety + index/log/hot for consumer visibility. |
| 7 | FEEL | A page that compounds — useful in 6 months, not just today. Skip filler; favor synthesis over transcription. |
| 8 | ACCEPT | Not every claim is wiki-worthy. Editorial judgment is part of ingest, not a bug to remove. |
| 9 | CREATE | Source + entity + concept pages with full frontmatter; cross-references; contradiction callouts where needed. |
| 10 | GROW | Contradictions found mid-ingest are the most valuable wiki signal. File them as questions for follow-up, not silently. |
