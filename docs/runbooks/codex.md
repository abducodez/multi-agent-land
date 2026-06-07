# Codex Runbook

This repo has project-local Codex setup:

- `AGENTS.md` contains durable repo instructions.
- `.codex/config.toml` raises the project instruction byte limit, uses `CLAUDE.md` as a fallback instruction file, keeps sandboxing conservative, and enables hooks.
- `.codex/hooks/stop_check.py` prints wrap-up reminders when code/config changes may need tests, docs, or UI verification.

## Verify Instruction Loading

From the repository root:

```bash
codex --ask-for-approval never "Summarize the active project instructions."
```

Expected: Codex should mention `AGENTS.md`, hackathon prize fit, event-ledger architecture, `uv` commands, docs rules, and the Codex co-author trailer.

## Trust Project Config

Codex loads `.codex/config.toml` and project-local hooks only after the project is trusted. If the hook does not appear, trust the repository in Codex and restart the session.

Review hooks with:

```text
/hooks
```

## Recommended Codex Workflow

1. Ask Codex to inspect relevant files before editing.
2. Make narrow changes that preserve the deterministic no-key path.
3. Update docs/ADRs/journal entries when public behavior or architecture changes.
4. Run:

   ```bash
   uv run pytest tests/ -q
   uv run ruff check .
   ```

5. Commit only when asked, with:

   ```text
   Co-authored-by: Codex <codex@openai.com>
   ```

## What Belongs In User Config Instead

Keep these out of the repo-scoped config:

- Model/provider/auth settings.
- Telemetry endpoints.
- Personal notification commands.
- Broad command auto-approval rules.
- Machine-specific writable roots.

