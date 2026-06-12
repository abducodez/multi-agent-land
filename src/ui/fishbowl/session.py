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

from src import observability as obs
from src.core.conductor import Conductor
from src.core.ledger_factory import make_ledger
from src.core.manifest import AgentManifest
from src.core.registry import Registry, default_registry
from src.tools.builtins import default_tool_registry
from src.ui.fishbowl.view_model import view_model_at

# Preferred display order, mirroring root app.py.  Any other scenarios dropped into
# config/ follow in sorted order.
_PREFERRED = [
    "thousand-token-wood",
    "mystery-roots",
    "oracle-grove",
    "the-steeped",
    "debate-duel",
    "twenty-sprouts",
    "beat-battle",
]


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
        obs.log(
            "session.created",
            scenario=scenario_name,
            ledger=type(self.conductor.ledger).__name__,
            cast=len(scenario.agents),
        )

    # ── lifecycle ───────────────────────────────────────────────────────────────

    def reset(self, seed: str = "", *, session_id: str | None = None) -> None:
        obs.log("session.reset", scenario=self._scenario_name, seed=seed or self.scenario.default_seed)
        self.conductor.reset(seed or self.scenario.default_seed, session_id=session_id)

    def step(self, n_ticks: int = 1) -> None:
        with obs.span("session.step", **{"session.n_ticks": n_ticks}):
            obs.log("session.step", n_ticks=n_ticks)
            self.conductor.step(n_ticks)

    def step_one(self) -> bool:
        """Advance a single agent (streaming): one event per call so the Show reveals
        each mind the moment it responds, not after the whole turn finishes."""
        with obs.span("session.step", **{"session.streaming": True}):
            return self.conductor.step_one()

    def inject(self, text: str, label: str | None = None) -> None:
        obs.log("session.inject", label=label or "", chars=len(text))
        obs.log("session.inject.text", level="debug", label=label or "", text=text)
        self.conductor.inject_user_event(text, label=label)
        self.conductor.step()

    # ── read surface (feeds view_model_at) ────────────────────────────────────────

    @property
    def scenario(self):
        return self.conductor.scenario

    # Marks the live, generative session apart from a read-only ``ReplaySession``;
    # the autoplay loop checks this so loading a past run never spends tokens.
    replay = False

    @property
    def events(self):
        # Run-scoped: the ledger is a shared store of *every* run (ADR-0009), so the
        # Show must only ever see the current run's events — otherwise scenario B's
        # stage would replay scenario A's discussion.  Every read below (head,
        # snapshot, scrubber) flows from this, so scoping here scopes the whole Show.
        return self.conductor.ledger.events_for_run(self.conductor.run_id)

    @property
    def head(self) -> int:
        """The generation-head: number of events in *this run* so far."""
        return len(self.events)

    def has_verdict(self) -> bool:
        """True once a ``judge.verdict`` event sits in *this run* — the show resolved.

        Run-scoped (ADR-0009): the ledger is a shared, append-only store of every run,
        so we only consult the current run's events.  The Fishbowl autoplay loop calls
        this to auto-pause the timer when the Judge has ruled, so the curtain falls on
        its own (no extra token spend)."""
        return any(e.kind == "judge.verdict" for e in self.conductor.ledger.events_for_run(self.conductor.run_id))

    def finalize(self, reason: str) -> None:
        """Close the current run with a ``run.finished`` event (idempotent-safe).

        On a verdict we derive ``winner`` from the judge's ruling (the ``winner``
        payload key) and ``winning_model`` from the run.started cast map.  The winner
        may be a cast *agent name* (judged scenarios → maps straight to its model) or a
        *team label* (versus scenarios, e.g. ``"herd"``).  For a team we attribute the
        model only when the team has exactly one member; multi-member teams have no
        single winning model (the seat, not a model, won) — the leaderboard credits the
        team.  Everything falls back to ``None`` when unknown."""
        winner: str | None = None
        winning_model: str | None = None
        run_events = self.conductor.ledger.events_for_run(self.conductor.run_id)
        if reason == "verdict":
            verdict = next((e for e in reversed(run_events) if e.kind == "judge.verdict"), None)
            if verdict is not None:
                winner = verdict.payload.get("winner") or None
            if winner:
                started = next((e for e in run_events if e.kind == "run.started"), None)
                started_payload = started.payload if started is not None else {}
                cast = started_payload.get("cast") or {}
                teams = (started_payload.get("competition") or {}).get("teams") or {}
                if winner in cast:
                    winning_model = (cast.get(winner) or {}).get("model_endpoint")
                elif winner in teams and len(teams[winner]) == 1:
                    winning_model = (cast.get(teams[winner][0]) or {}).get("model_endpoint")
        self.conductor.finalize(reason, winner=winner, winning_model=winning_model)

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

    @property
    def autoplay_tick_cap(self) -> int:
        """Hard backstop on consecutive autoplay generations, derived from the budget.

        The governor (max_turns / max_total_tokens / max_total_calls) is the real bound
        on how long a show runs — this cap only exists to stop an *unbounded* loop if
        those are misconfigured. We size it just above the total-call ceiling so a
        legitimately long show (a late Judge verdict) always resolves on a real budget
        bound with a meaningful reason, never on this arbitrary backstop."""
        return max(120, int(getattr(self.governor, "max_total_calls", 0) or 0) + 10)

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


class ReplaySession:
    """A read-only view over one *past* run — the Archive's "Load" target.

    It exposes the exact read surface the Show renders (``events`` / ``head`` /
    ``snapshot`` / ``has_verdict``) so the transport's scrubber and replay just work,
    but it owns no live ``Conductor``: ``step``/``step_one``/``inject`` are no-ops and
    ``replay`` is ``True``, so autoplay never generates (no token spend) on a load.

    The fixed event list is the run's own slice (``events_for_run(run_id)``); the cast
    cards / meters bounds are rebuilt from that run's scenario via the registry, while
    the discussion itself is replayed verbatim from the events.
    """

    replay = True

    def __init__(
        self,
        *,
        run_id: str,
        events: tuple,
        scenario_name: str,
        registry: Registry | None = None,
        tools=None,
    ) -> None:
        self.run_id = run_id
        self._events = tuple(events)
        self._scenario_name = scenario_name
        registry = registry or default_registry()
        tools = tools if tools is not None else default_tool_registry()
        scenario = registry.build_scenario(scenario_name, tools=tools)
        self._scenario = scenario
        self._cast = [agent.manifest for agent in scenario.agents]
        self._governor = registry.governor_for(scenario_name)
        obs.log("session.replay", scenario=scenario_name, run_id=run_id, events=len(self._events))

    # ── read surface (mirrors FishbowlSession) ────────────────────────────────────

    @property
    def events(self):
        return self._events

    @property
    def head(self) -> int:
        return len(self._events)

    @property
    def scenario_name(self) -> str:
        return self._scenario_name

    @property
    def goal(self) -> str:
        return self._scenario.goal

    @property
    def cast(self) -> list[AgentManifest]:
        return self._cast

    def has_verdict(self) -> bool:
        return any(e.kind == "judge.verdict" for e in self._events)

    @property
    def autoplay_tick_cap(self) -> int:
        return self.head

    # ── inert lifecycle (a replay never generates) ────────────────────────────────

    def reset(self, *_args, **_kwargs) -> None:  # pragma: no cover - inert by design
        return None

    def step(self, *_args, **_kwargs) -> None:
        return None

    def step_one(self, *_args, **_kwargs) -> bool:
        return False

    def inject(self, *_args, **_kwargs) -> None:
        return None

    # ── snapshot ──────────────────────────────────────────────────────────────────

    def snapshot(self, k: int | None = None) -> dict:
        events = self._events
        return view_model_at(
            events,
            k if k is not None else len(events),
            self._cast,
            scenario_name=self._scenario_name,
            goal=self.goal,
            governor=None,  # no live governor on a replay — meters show recorded text only
            token_ceiling=getattr(self._governor, "max_total_tokens", None),
            max_rounds=getattr(self._governor, "max_turns", None),
        )
