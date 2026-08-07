# brain-wiki — Claude + Obsidian Wiki Vault

This folder is both a Claude Code plugin and an Obsidian vault.

**Plugin name:** `brain-wiki` (v1.7+ "Compound Vault" — see [docs/compound-vault-guide.md](docs/compound-vault-guide.md); v1.8+ adds methodology modes — see [docs/methodology-modes-guide.md](docs/methodology-modes-guide.md))
**Skills:** `/wiki`, `/wiki-ingest`, `/wiki-query`, `/wiki-lint`, `/wiki-cli` (v1.7), `/wiki-retrieve` (v1.7, opt-in), `/wiki-mode` (v1.8), `/corpus-query` (local)
**Vault path:** This directory (open in Obsidian directly)

## What This Vault Is For

This vault demonstrates the LLM Wiki pattern — a persistent, compounding knowledge base for Claude + Obsidian. Drop any source, ask any question, and the wiki grows richer with every session.

## Vault Structure

```
.raw/           source documents — immutable, Claude reads but never modifies
wiki/           Claude-generated knowledge base
_templates/     Obsidian Templater templates
_attachments/   images and PDFs referenced by wiki pages
```

## How to Use

Drop a source file into `.raw/`, then tell Claude: "ingest [filename]".

Ask any question. Claude reads the index first, then drills into relevant pages.

Run `/wiki` to scaffold a new vault or check setup status.

Run "lint the wiki" every 10-15 ingests to catch orphans and gaps.

## Cross-Project Access

To reference this wiki from another Claude Code project, add to that project's CLAUDE.md:

```markdown
## Wiki Knowledge Base
Path: /path/to/this/vault

When you need context not already in this project:
1. Read wiki/hot.md first (recent context, ~500 words)
2. If not enough, read wiki/index.md
3. If you need domain specifics, read wiki/<domain>/_index.md
4. Only then read individual wiki pages

Do NOT read the wiki for general coding questions or things already in this project.
```

## Plugin Skills

| Skill | Trigger |
|-------|---------|
| `/wiki` | Setup, scaffold, route to sub-skills |
| `ingest [source]` | Single or batch source ingestion |
| `query: [question]` | Answer from wiki content |
| `lint the wiki` | Health check |
| `/save` | File the current conversation as a structured wiki note |
| `/autoresearch [topic]` | Autonomous research loop: search, fetch, synthesize, file |
| `/canvas` | Visual layer: add images, PDFs, notes to Obsidian canvas |
| `/wiki-cli` (v1.7) | Obsidian CLI transport wrapper; default mutation path on desktop |
| `/wiki-retrieve` (v1.7) | Hybrid contextual + BM25 + cosine-rerank retrieval (opt-in via `bash bin/setup-retrieve.sh`) |
| `/wiki-mode` (v1.8) | Methodology modes (LYT / PARA / Zettelkasten / Generic). Set via `bash bin/setup-mode.sh`; consumed by wiki-ingest / save / autoresearch for routing new pages |
| `/corpus-query` (local) | Answer from the PRIMARY SOURCE documents under `.sources/`, not the curated wiki. Separate tier-2 BM25 + rerank index; fully on-machine. Use when a wiki page is thin or the exact original wording matters |
| `/think` (v1.9) | The 10-principle thinking loop (OBSERVE-OBSERVE-LISTEN-THINK-CONNECT-CONNECT-FEEL-ACCEPT-CREATE-GROW) as an invocable workflow. Apply to architectural decisions, audits, post-mortems, ambiguous user requests. Every other skill has a "How to think" appendix mapping this framework to its specific work |

## Transport (v1.7+)

`scripts/detect-transport.sh` writes `.vault-meta/transport.json` on first run and refreshes weekly. Skills consult it before mutating the vault. Fallback chain: Obsidian CLI → mcp-obsidian → mcpvault → filesystem (always-available floor). Decision tree: [wiki/references/transport-fallback.md](wiki/references/transport-fallback.md).

## Concurrency (v1.7+)

`scripts/wiki-lock.sh` provides per-file advisory locks for safe multi-writer ingest. Every wiki page write should be guarded by `wiki-lock acquire`/`release`. Stale-after default is 60s; cross-process release allowed by design. The PostToolUse hook defers `git add` while locks are held. Closes the latent multi-writer corruption hole from v1.6.

## Methodology Modes (v1.8+)

Pick an organizational style for the vault via `bash bin/setup-mode.sh`. Four modes available: **generic** (v1.7 default — no opinion), **LYT** (Linking Your Thinking — MOCs + atomic notes), **PARA** (Projects/Areas/Resources/Archives), **Zettelkasten** (timestamped IDs, flat, dense linking). The mode is written to `.vault-meta/mode.json` (gitignored by default; `git add -f` to commit). `wiki-ingest`, `save`, and `autoresearch` consult `python3 scripts/wiki-mode.py route <type> "<name>"` before filing new pages — no special-casing needed in the consumer skills. Full guide: [docs/methodology-modes-guide.md](docs/methodology-modes-guide.md). Closes priority gap 5 from the May 2026 compass artifact.

## Pre-commit verifier (v1.7.1+)

After staging changes for a non-trivial workstream but BEFORE running `git commit`, dispatch the `verifier` agent (`agents/verifier.md`). It reads `git diff --cached`, applies the /best-practices six-cut + agent kernel, and returns findings in four tiers (BLOCKER / HIGH / MEDIUM / LOW) with file:line citations. The agent has read-only tools (Read, Grep, Glob, Bash) — it can inspect but never modify, so its output is purely advisory. This closes the loop the v1.7 audit revealed: code went worker → commit with no separate verifier pass, which is how BLOCKER B1 (data-egress consent gap) slipped through. See `docs/audits/v1.7.0-audit-2026-05-17.md` §10 for the retrospective.

## MCP (Optional)

If you configured the MCP server, Claude can read and write vault notes directly.
See `skills/wiki/references/mcp-setup.md` for setup instructions.

## Release Blog Post

After cutting a new release (git tag + `gh release create`), run:

```
/release-blog
```

This generates a blog post on https://agricidaniel.com/blog/, handles cover image generation, SEO metadata, FAQ schema, internal linking, sitemap/llms.txt updates, Vercel deployment, and Google indexing.

## Vault-Local Ingest Convention: raw_file Provenance (added 2026-07-26)

This vault extends the stock `wiki-ingest` flow with a mandatory provenance rule. `.raw/` is the inbox (upstream convention — files there are immutable). `.sources/` is the full-text archive that backs `raw_file:` drill-down (git-ignored, invisible to Obsidian).

1. **Binary sources** (PDF, DOCX, PPTX, XLSX, …) dropped in `.raw/`: convert BEFORE ingesting.
   **Default converter: docling** (layout-aware — real paragraphs, headings, Markdown tables):
   `docling "<file>" --image-export-mode placeholder --output "<vault>/.sources/Ingested/"`
   then rename the output to keep the original extension in the name (e.g. `report.pdf.md`). ALWAYS pass `--image-export-mode placeholder` (default embeds images as huge base64 blobs). Fallback: `markitdown` for formats docling can't handle. Read the converted md for the ingest, leave the original in `.raw/` untouched, and set the source page's frontmatter:
   `raw_file: "<vault>/.sources/Ingested/<name>.<origext>.md"` (absolute forward-slash path)
   **QA gate (mandatory after every conversion):** run `bash bin/review-conversion.sh "<converted.md>" "<original file>"` — an independent claude-CLI review that Reads BOTH the original document (PDFs as rendered pages) and the converted md, verifying completeness (nothing dropped), accuracy (numbers/tables spot-checked, no column interleaving), human-readability, and machine-readability. Always pass the original when it exists (fidelity mode also distinguishes conversion errors from errors present in the source); md-only mode is the fallback when the original is unavailable. Exit 0 (PASS) → proceed to ingest; note any listed issues in the source page's Notes section if material. Exit 1 (FAIL) → follow the RECOMMENDATION line (usually the LLM finishing layer below; re-run the review after fixing). Exit 2 → tooling error, fix before proceeding.
   **LLM finishing layer (triggered by QA FAIL, or opt-in per document):** for scanned PDFs (docling output empty/garbled — no text layer) or documents the operator wants to read cover-to-cover, produce the md by reading the original directly (Claude can read PDFs) and writing clean natural Markdown; add `converted_by: claude` to a comment at the top of the file. Docling output may also be polished this way — fix spacing artifacts, reflow, restore split words; never alter content.
2. **Text/Markdown sources** already in `.raw/` (including URL ingests saved to `.raw/articles/`): no conversion; set `raw_file:` to the absolute forward-slash path of the `.raw/` file itself.
3. **`raw_file:` is MANDATORY on every new source page.** Verify the file exists at that path before finishing the ingest. This is what makes full-text drill-down work (see `wiki/hot.md` → Full-Text Drill-Down).
4. Any pre-existing archive corpus in other `.sources/` subfolders is read-only — never reorganize or modify it. New conversions go only in `.sources/Ingested/`.
