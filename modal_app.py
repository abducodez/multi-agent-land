"""Optional serverless deployment — run a scenario on a schedule with Modal.

The ledger lives on a persistent Modal Volume, so each scheduled invocation
restores the run and advances it by one episode: the long-running story (ADR-0013,
docs/architecture/long-running.md) deployed.  Not imported by the app or tests —
used only when you deploy it:

    modal run    modal_app.py        # one-off episode
    modal deploy modal_app.py        # schedule it (hourly by default)
"""
from __future__ import annotations

import modal

APP_NAME = "multi-agent-land"
DEFAULT_SCENARIO = "thousand-token-wood"
TICKS_PER_EPISODE = 60

# Mount the whole repo (code + config/) so the registry resolves config via the
# same __file__ logic it uses locally.  Deps come from pyproject.toml (single
# source of truth, pinned locally by uv.lock).
image = (
    modal.Image.debian_slim()
    .pip_install_from_pyproject("pyproject.toml")
    .add_local_dir(
        ".",
        remote_path="/root",
        ignore=["__pycache__", "*.pyc", ".venv", ".git", "runs", "*.db", ".pytest_cache", ".ruff_cache"],
    )
)

app = modal.App(APP_NAME)
volume = modal.Volume.from_name("multi-agent-land-runs", create_if_missing=True)


@app.function(image=image, volumes={"/data": volume}, schedule=modal.Cron("0 * * * *"))
def run_episode(scenario_name: str = DEFAULT_SCENARIO, n_ticks: int = TICKS_PER_EPISODE) -> dict:
    from pathlib import Path

    from src.core.conductor import Conductor
    from src.core.registry import default_registry
    from src.core.sqlite_ledger import SQLiteLedger
    from src.tools.builtins import default_tool_registry

    db_path = f"/data/{scenario_name}.db"
    reg = default_registry()
    ledger = SQLiteLedger.from_file(db_path) if Path(db_path).exists() else SQLiteLedger(db_path)
    conductor = Conductor(
        reg.build_scenario(scenario_name, tools=default_tool_registry()),
        governor=reg.governor_for(scenario_name),
        ledger=ledger,
        snapshot_every=20,
        snapshot_path=f"/data/{scenario_name}.snapshot.db",
    )

    if not conductor.restore():
        conductor.reset(conductor.scenario.default_seed)
    conductor.step(n_ticks=n_ticks)
    ledger.close()
    volume.commit()  # persist the ledger for the next scheduled run
    return {"scenario": scenario_name, "turn": conductor.turn, "stats": conductor.governor.stats}


@app.local_entrypoint()
def main() -> None:
    print(run_episode.remote())
