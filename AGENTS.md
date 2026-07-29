# Agent Instructions

This project uses **bd** (beads) for issue tracking. Run `bd onboard` to get started.

## ⚠️ Beads issues are local-only right now

The JSONL export is broken (`.beads/beads.jsonl` does not exist; `bd doctor` reports
"Could not read JSONL file"). Issue data lives in the local Dolt database under
`.beads/dolt/`, which is gitignored — **a fresh clone sees zero issues**.

Consequences:
- `bd ready` / `bd show` only work in a working copy that already has the Dolt DB
- `bd sync` in the workflow below will not round-trip issues through git until this
  is fixed
- Anything you learn that matters beyond one session belongs in `README.md` or
  `plan.md`, not only in an issue

## Where the durable context lives

- `README.md` — architecture, which pipeline steps are done, data sources
- `plan.md` — working design for steps 5-7: the research agent's endpoint and
  request shape, the anti-leakage prompt, the pilot's test cases
- `.env.example` — required credentials and the `az` commands that retrieve them
- `archive/tavily-pipeline-2026-07-28` — the abandoned Tavily/scraper pipeline;
  `extract_qualitative.py` there has a JSON schema, system prompt and `validate()`
  worth lifting for step 6

## Quick Reference

```bash
bd ready              # Find available work
bd show <id>          # View issue details
bd update <id> --status in_progress  # Claim work
bd close <id>         # Complete work
bd sync               # Sync with git
```

## Landing the Plane (Session Completion)

**When ending a work session**, you MUST complete ALL steps below. Work is NOT complete until `git push` succeeds.

**MANDATORY WORKFLOW:**

1. **File issues for remaining work** - Create issues for anything that needs follow-up
2. **Run quality gates** (if code changed) - Tests, linters, builds
3. **Update issue status** - Close finished work, update in-progress items
4. **PUSH TO REMOTE** - This is MANDATORY:
   ```bash
   git pull --rebase
   bd sync
   git push
   git status  # MUST show "up to date with origin"
   ```
5. **Clean up** - Clear stashes, prune remote branches
6. **Verify** - All changes committed AND pushed
7. **Hand off** - Provide context for next session

**CRITICAL RULES:**
- Work is NOT complete until `git push` succeeds
- NEVER stop before pushing - that leaves work stranded locally
- NEVER say "ready to push when you are" - YOU must push
- If push fails, resolve and retry until it succeeds

