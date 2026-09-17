---
description: Guided first-run for a fresh vault — five skippable questions, then the wiki is built from the answers. Resumable.
---

> Project-scope copy of `commands/quickstart.md` so a cloned vault works without the
> plugin installed. Skill names below resolve to `skills/<name>/SKILL.md` at the
> vault root — read that file by path if the skill is not registered.

Read the `quickstart` skill (`skills/quickstart/SKILL.md` at the vault root) and follow it from the top: check
`scripts/quickstart-state.py status` first (resume an unfinished run rather
than restarting), otherwise offer the gate question and, on yes, run the
five-question interview and the build sequence.

If the user declines the gate question, read the `wiki` skill (`skills/wiki/SKILL.md` at the vault root) and continue
with its standard setup instead.
