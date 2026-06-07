#!/usr/bin/env python3
from __future__ import annotations

import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def run(args: list[str]) -> str:
    result = subprocess.run(args, cwd=ROOT, text=True, capture_output=True, check=False)
    return result.stdout.strip()


def main() -> int:
    changed = run(["git", "status", "--short"])
    if not changed:
        return 0

    paths = [line[3:] for line in changed.splitlines() if len(line) > 3]
    code_like = [
        path
        for path in paths
        if path.endswith((".py", ".yaml", ".yml", ".toml", ".json")) and not path.startswith(".codex/")
    ]
    docs_like = [path for path in paths if path.startswith("docs/") or path in {"README.md", "AGENTS.md", "CLAUDE.md"}]
    tests_like = [path for path in paths if path.startswith("tests/")]

    reminders: list[str] = []
    if code_like and not tests_like:
        reminders.append("code/config changed; verify whether focused tests should be added or updated")
    if code_like and not docs_like:
        reminders.append("code/config changed; verify whether docs, ADRs, schema docs, or journal need updates")
    if any(path.startswith("app.py") or path.startswith("src/ui/") for path in paths):
        reminders.append("UI changed; verify the Gradio app manually when a browser/server is available")

    if reminders:
        print("[codex-stop-check] Wrap-up reminders:", file=sys.stderr)
        for reminder in reminders:
            print(f"- {reminder}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
