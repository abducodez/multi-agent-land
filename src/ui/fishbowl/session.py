"""``FishbowlSession`` — a thin wrapper over one live :class:`Conductor`.

The Fishbowl app shell holds one of these per browser session (in ``gr.State`` or
keyed by ``gr.Request.session_hash``).  This class owns no Gradio components; it is
plain Python that builds the engine exactly as the root ``app.py`` does — via
``default_registry()`` / ``build_scenario`` / ``governor_for`` / ``make_ledger`` —
and exposes the read surface ``view_model_at`` needs.

Everything stays offline-safe: with no API key the deterministic stub drives the
cast, so a session is reproducible on stage.
"""

from __future__ import annotations

from src.core.conductor import Conductor
from src.core.ledger_factory import make_ledger
from src.core.manifest import AgentManifest
from src.core.registry import Registry, default_registry
from src.tools.builtins import default_tool_registry
from src.ui.fishbowl.view_model import view_model_at

# Preferred display order, mirroring root app.py.  Any other scenarios dropped into
# config/ follow in sorted order.
_PREFERRED = ["thousand-token-wood", "mystery-roots", "oracle-grove"]


def _ordered_names(registry: Registry) -> list[str]:
    return [n for n in _PREFERRED if n in registry.scenarios] + [
        n for n in sorted(registry.scenarios) if n not in _PREFERRED
    ]


def scenario_titles(registry: Registry | None = None) -> dict[str, str]:
    """Map display title → internal scenario name, in the app's preferred order."""
    registry = registry or default_registry()
    return {(registry.scenarios[n].title or n): n for n in _ordered_names(registry)}


class FishbowlSession:
    """Owns one live ``Conductor`` for a chosen scenario and feeds the presenter."""

    def __init__(self, scenario_name: str, *, registry: Registry | None = None, tools=None) -> None:
        self._registry = registry or default_registry()
        self._scenario_name = scenario_name
        tools = tools if tools is not None else default_tool_registry()
        scenario = self._registry.build_scenario(scenario_name, tools=tools)
        self.conductor = Conductor(
            scenario,
            governor=self._registry.governor_for(scenario_name),
            ledger=make_ledger(),
        )

    # ── lifecycle ───────────────────────────────────────────────────────────────

    def reset(self, seed: str = "") -> None:
        self.conductor.reset(seed or self.scenario.default_seed)

    def step(self, n_ticks: int = 1) -> None:
        self.conductor.step(n_ticks)

    def inject(self, text: str, label: str | None = None) -> None:
        self.conductor.inject_user_event(text, label=label)
        self.conductor.step()

    # ── read surface (feeds view_model_at) ────────────────────────────────────────

    @property
    def scenario(self):
        return self.conductor.scenario

    @property
    def events(self):
        return self.conductor.ledger.events

    @property
    def head(self) -> int:
        """The generation-head: number of events in the ledger so far."""
        return len(self.conductor.ledger.events)

    def has_verdict(self) -> bool:
        """True once a ``judge.verdict`` event sits in the ledger — the show resolved.

        The Fishbowl autoplay loop consults this to auto-pause the timer when the
        Judge has ruled, so the curtain falls on its own (no extra token spend)."""
        return any(getattr(e, "kind", None) == "judge.verdict" for e in self.conductor.ledger.events)

    @property
    def cast(self) -> list[AgentManifest]:
        return [agent.manifest for agent in self.scenario.agents]

    @property
    def governor(self):
        return self.conductor.governor

    @property
    def scenario_name(self) -> str:
        return self._scenario_name

    @property
    def goal(self) -> str:
        return self.scenario.goal

    @property
    def token_ceiling(self) -> int | None:
        return self.governor.max_total_tokens

    @property
    def max_rounds(self) -> int:
        return self.governor.max_turns

    # ── snapshot ──────────────────────────────────────────────────────────────────

    def snapshot(self, k: int | None = None) -> dict:
        """Build the Show's view-model at step *k* (defaults to the head)."""
        events = self.events
        return view_model_at(
            events,
            k if k is not None else len(events),
            self.cast,
            scenario_name=self.scenario_name,
            goal=self.goal,
            governor=self.governor,
            token_ceiling=self.token_ceiling,
            max_rounds=self.max_rounds,
        )
