# CLAUDE.md

See **[AGENTS.md](AGENTS.md)** — the canonical shared guidance for this project (what it is,
repo layout, the non-negotiable governance rules, and architecture invariants). It applies to
Claude Code and every other agent tool.

Python backend coding standards + toolchain: **[backend/CLAUDE.md](backend/CLAUDE.md)**.
Frontend (Next.js) coding standards + toolchain: **[web/OVERVIEW.md](web/OVERVIEW.md)** (the one
source; `web/CLAUDE.md` only imports it, and `web/AGENTS.md` holds the Next.js-managed block).

**Two things to internalize before any work:**
1. The spec is authoritative — read `docs/backend/` (esp. the governance rules in `AGENTS.md` and
   `docs/backend/modules/25-governance-enforcement.md`) before coding.
2. `backend/.env` holds **live secrets** — never read its values aloud, echo, or commit them.
