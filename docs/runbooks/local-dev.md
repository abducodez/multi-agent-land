# Local Development Runbook

## Start

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python app.py
```

## Journal

```bash
python scripts/new_journal_entry.py "Scaffolded walking skeleton"
python scripts/snapshot_progress.py
```

## Checks

```bash
python -m compileall app.py src scripts
```

Add formal tests once the first persistent ledger and provider adapters land.

