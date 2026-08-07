# Attributions

brain-wiki is derived from **claude-obsidian** by AgriciDaniel (MIT — see the
final section below). The following third-party patterns, tools, and creators
informed the design of the toolkit and its extensions.

---

## LLM Wiki Pattern

**Author:** Andrej Karpathy
**Source:** https://github.com/karpathy
**Use:** The core architecture of claude-obsidian — using an LLM to build and maintain a structured wiki from raw sources — is based on the LLM Wiki pattern Karpathy described publicly. claude-obsidian is an independent implementation; no code or content from Karpathy's repositories was copied.

---

## llm-wiki-newsroom Patterns

**Author:** alfadur7
**Source:** https://github.com/alfadur7/llm-wiki-newsroom
**License:** MIT
**Use:** Three maintenance-loop design patterns in this toolkit are adapted from llm-wiki-newsroom's harness design: the **reground loop** (stale pages re-enter the pipeline as input — implemented here as the work queue, `scripts/work-queue.py`), the **defect-log meta loop** (recurring ingest failures generate operator-approved guideline proposals; the loop proposes, never self-adopts — wiki-lint §Defect Log & Meta Loop), and the **inbox queue** (`.raw/_inbox.md`, wiki-ingest §Inbox). These are independent implementations of the patterns; no code was copied.

---

## ITS CSS Snippets

**Author:** SlRvb
**Source:** https://github.com/SlRvb/Obsidian--ITS-Theme
**License:** GPL-2.0
**Files:**
- `.obsidian/snippets/ITS-Dataview-Cards.css`
- `.obsidian/snippets/ITS-Image-Adjustments.css`

These snippets are distributed under the GPL-2.0 license. Per GPL-2.0 terms, any modifications to these files must also be released under GPL-2.0.

---

## Obsidian Plugins (pre-installed)

The following Obsidian community plugins ship with this vault as pre-installed binaries. They are the property of their respective authors and are distributed here solely to reduce setup friction. Users should verify license terms via each plugin's repository.

| Plugin | Author | Repository |
|--------|--------|-----------|
| Calendar | Liam Cain | https://github.com/liamcain/obsidian-calendar-plugin |
| Thino | Boninall (Quorafind) | https://github.com/Quorafind/Obsidian-Thino |
| Obsidian Excalidraw | Zsolt Viczian | https://github.com/zsviczian/obsidian-excalidraw-plugin |
| Obsidian Banners | Danny Hernandez | https://github.com/noatpad/obsidian-banners |

`obsidian-excalidraw-plugin/main.js` is **not** included in this repository. It is downloaded automatically by `bin/setup-vault.sh` from the plugin's official GitHub releases.

---

## Upstream: claude-obsidian

**Author:** AgriciDaniel / AI Marketing Hub
**License:** MIT (see [LICENSE](LICENSE))
**Repository:** https://github.com/AgriciDaniel/claude-obsidian
**Use:** brain-wiki is a derived work of claude-obsidian v1.9.2 — the Compound
Vault architecture, DragonScale multi-writer machinery, methodology modes,
thinking framework, and the stock skills/scripts/tests originate there. The
v2.0 professional-corpus layer (corpus retrieval tier, conversion pipeline,
three-query surface, default-on hybrid retrieval) was developed in this fork.
The original copyright notice is retained in [LICENSE](LICENSE).

---

## Conversion Engines (v2.0 corpus layer)

The conversion pipeline (`scripts/convert.py` / `scripts/convert_formats.py`)
calls these third-party libraries when installed; none are bundled:

| Library | Author / Project | License | Used for |
|---------|-----------------|---------|----------|
| docling | IBM Research / DS4SD | MIT | PDF and DOCX → Markdown |
| python-pptx | Steve Canny | MIT | PPTX → Markdown (slide anchors, speaker notes) |
| markdownify | Matthew Dapena-Tretter | MIT | HTML → Markdown |
| html2text | Aaron Swartz et al. | GPL-3.0 | HTML → Markdown (fallback engine) |
| extract-msg | Destiny Peterson & The Elemental of Destruction | GPL-3.0 | Outlook .msg parsing (optional) |

The GPL-licensed libraries are optional runtime dependencies invoked as
installed packages, never vendored into this repository.
