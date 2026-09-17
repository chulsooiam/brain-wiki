---
description: Bootstrap or check the brain-wiki wiki vault. Reads the wiki skill and runs setup workflow.
---

Read the `wiki` skill. Then run the setup workflow:

1. Check if Obsidian is installed. If not, offer to install it (see `skills/wiki/references/plugins.md`).
2. Check if this directory has a vault (look for `.obsidian/` folder). If yes, report current vault state.
3. Check if the MCP server is configured (`claude mcp list`). If not, ask if the user wants to set it up.
4. If this is a fresh vault — `wiki/hot.md` is missing OR still contains the seed line "The vault is freshly initialized" (the cloned repo ships that placeholder, so absence alone is the wrong test) — and `python3 scripts/quickstart-state.py status` prints `NOT_STARTED`, offer the guided quick start: read the `quickstart` skill and follow its gate question. An accepted quickstart replaces step 5 — its interview and build take over from here. A declined one costs one keystroke and continues below. An unfinished one (the skill's status check reports IN_PROGRESS) is offered as a resume.
5. Ask ONE question: "What is this vault for?"

Then build the entire wiki structure based on the answer. Don't ask more questions. Scaffold it, show what was created, and ask: "Want to adjust anything before we start?"

Examples of what the user might say:
- "Map the architecture of github.com/org/repo"
- "Build a sitemap and content analysis for example.com"
- "Track my SaaS business — product, customers, metrics, roadmap"
- "Research project on [topic] — papers, concepts, open questions"
- "Personal second brain — health, goals, learning, projects"
- "Organize my YouTube channel — transcripts, topics, tools mentioned"
- "Executive assistant brain — meetings, tasks, business context"

If the vault is already set up, skip to checking what has been ingested recently and offering to continue where things left off.
