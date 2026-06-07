# Local Development Runbook

## Start

```bash
uv sync          # create .venv and install from the lockfile
uv run app.py
```

## Journal

```bash
uv run scripts/new_journal_entry.py "Scaffolded walking skeleton"
uv run scripts/snapshot_progress.py
```

## Checks

```bash
uv run python -m compileall app.py src scripts
```

Add formal tests once the first persistent ledger and provider adapters land.

