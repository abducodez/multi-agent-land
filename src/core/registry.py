"""Agent + scenario registry — discovery and assembly from declarative config.

The registry is what makes "drop in a file to add an agent / pick the cast / wire
a tool" true.  It loads agent manifests and scenario configs from ``config/`` (or
any directory), resolves a scenario's ``cast`` of agent names into live agents,
and binds each agent to its model profile and tool grants.  No engine code names
an agent or a scenario — it reads config and routes.  See ADR-0011.

Most agents need no Python at all: a YAML manifest + the generic ManifestAgent is
enough.  Only agents with custom behaviour (tool calls, special prompts) register
a handler class via :func:`register_handler` and reference it by ``handler:`` in
their manifest.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from src import observability as obs
from src.agents.base import Agent, ManifestAgent
from src.core.config import (
    GovernorConfig,
    ModelsConfig,
    ScenarioConfig,
    WorldConfig,
    validate_agent,
    validate_scenario,
)
from src.core.governor import Governor
from src.core.manifest import AgentManifest
from src.models.router import ModelRouter, ProfileSpec
from src.scenarios.base import Scenario

_REPO_ROOT = Path(__file__).resolve().parents[2]
# Config location is itself configurable: MAL_CONFIG_DIR lets a container or an
# alternate deployment point the registry at a different config tree.
DEFAULT_CONFIG_DIR = Path(os.getenv("MAL_CONFIG_DIR") or _REPO_ROOT / "config")


_ENV_REF = re.compile(r"\$\{(\w+)\}|\$(\w+)")


def _expand_env(value):
    """Recursively expand ``$VAR`` / ``${VAR}`` in a loaded-config tree.

    Lets ``config/models.yaml`` point profiles at a Modal endpoint without
    hard-coding the workspace URL or key — e.g.
    ``base_url: https://${MODAL_WORKSPACE}--<endpoint>.modal.run/v1``.

    If *any* referenced var in a string is unset/empty, the whole string collapses
    to ``""`` — a binding built from a missing workspace is simply *not configured*
    rather than a half-templated, broken URL.  The validator then nulls it, and the
    offline path ignores live bindings entirely."""
    if isinstance(value, str):
        refs = _ENV_REF.findall(value)
        if refs and any(not os.getenv(g1 or g2, "") for g1, g2 in refs):
            return ""
        return _ENV_REF.sub(lambda m: os.getenv(m.group(1) or m.group(2), ""), value)
    if isinstance(value, dict):
        return {k: _expand_env(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_expand_env(v) for v in value]
    return value


def _resolve_model_endpoints(raw_models: dict, env: dict[str, str] | None = None) -> dict:
    """Expand each profile's ``endpoint:`` catalogue key into a concrete binding.

    A profile may bind to a model by its **catalogue key** instead of spelling out
    the model string and URL.  The key may name a model on either inference backend —
    a bare Modal endpoint slug, or a backend-qualified key (``hf:<repo>``)::

        profiles:
          tiny:    {endpoint: nemotron-3-nano-4b, temperature: 0.7, max_tokens: 160}
          balanced: {endpoint: "hf:google/gemma-2-9b-it", temperature: 0.8}

    For each such profile this fills ``model`` (``openai/<served_id>``), ``base_url``
    (Modal workspace URL, or the HF router), and ``api_key`` (the backend's token)
    from the owning catalogue, then drops the ``endpoint`` key so the result validates
    against :class:`ModelProfileConfig` (which forbids unknown fields). Precedence: a
    ``MODEL_<PROFILE>`` env var wins for the model string; otherwise explicit
    ``model`` / ``base_url`` / ``api_key`` in the YAML win over the derived values. A
    profile with an explicit ``model`` and no ``endpoint`` passes through untouched.
    """
    from src.models import inference

    source = os.environ if env is None else env
    profiles = raw_models.get("profiles")
    if not isinstance(profiles, dict):
        return raw_models
    for profile, cfg in profiles.items():
        if not isinstance(cfg, dict) or "endpoint" not in cfg:
            continue
        binding = inference.binding_for(cfg.pop("endpoint"), env=source)
        override = source.get(f"MODEL_{str(profile).upper()}", "").strip()
        if override:
            cfg["model"] = override  # MODEL_<PROFILE> is the highest-priority override
        else:
            cfg.setdefault("model", binding["model"])
        cfg.setdefault("base_url", binding["base_url"])
        cfg.setdefault("api_key", binding["api_key"])
        obs.log(
            "registry.model_endpoint",
            profile=str(profile),
            model=cfg.get("model", ""),
            base_url=cfg.get("base_url", ""),
        )  # never logs api_key
    return raw_models


# ── handler registry (behaviour bindings) ────────────────────────────────────────

HANDLERS: dict[str, type[ManifestAgent]] = {}


def register_handler(name: str):
    """Class decorator: register a ManifestAgent subclass under *name*.

    A manifest with ``handler: <name>`` is instantiated from this class; its
    declarative fields still come from the YAML, so the handler only supplies
    behaviour (tool calls, custom prompt logic)."""

    def _decorator(cls: type[ManifestAgent]) -> type[ManifestAgent]:
        HANDLERS[name] = cls
        return cls

    return _decorator


# ── registry ─────────────────────────────────────────────────────────────────────


@dataclass
class Registry:
    agents: dict[str, AgentManifest] = field(default_factory=dict)
    scenarios: dict[str, ScenarioConfig] = field(default_factory=dict)
    models: ModelsConfig = field(default_factory=ModelsConfig)

    # ── loading ──────────────────────────────────────────────────────────────

    @classmethod
    def from_dir(cls, root: Path | str = DEFAULT_CONFIG_DIR) -> "Registry":
        """Load agents/*.yaml, scenarios/*.yaml, and models.yaml from *root*."""
        root = Path(root)
        agents: dict[str, AgentManifest] = {}
        agents_dir = root / "agents"
        if agents_dir.is_dir():
            for path in sorted(agents_dir.glob("*.yaml")):
                manifest = validate_agent(yaml.safe_load(path.read_text()) or {})
                agents[manifest.name] = manifest
                obs.log(
                    "manifest.loaded",
                    agent=manifest.name,
                    profile=manifest.model_profile,
                    endpoint=manifest.model_endpoint or "",
                    subscribes=len(manifest.subscribes_to),
                    may_emit=list(manifest.may_emit),
                )

        scenarios: dict[str, ScenarioConfig] = {}
        scenarios_dir = root / "scenarios"
        if scenarios_dir.is_dir():
            for path in sorted(scenarios_dir.glob("*.yaml")):
                scenario = validate_scenario(yaml.safe_load(path.read_text()) or {})
                scenarios[scenario.name] = scenario

        models = ModelsConfig()
        models_file = root / "models.yaml"
        if models_file.is_file():
            raw_models = _expand_env(yaml.safe_load(models_file.read_text()) or {})
            raw_models = _resolve_model_endpoints(raw_models)
            models = ModelsConfig.model_validate(raw_models)

        obs.log("config.loaded", config_dir=str(root), agents=len(agents), scenarios=len(scenarios))
        return cls(agents=agents, scenarios=scenarios, models=models)

    @classmethod
    def from_world(cls, world: WorldConfig) -> "Registry":
        """Build an in-memory registry from a composed, validated :class:`WorldConfig`.

        The in-memory mirror of :meth:`from_dir`: agents, scenarios, and model
        bindings come straight off the world object instead of ``config/``.  So a
        run composed by the Lab (or an LLM) flows through the exact same
        ``build_scenario`` / ``build_router`` / ``governor_for`` path as a
        config-file run — emit a world, validate it, run it.  See ADR-0011 / ADR-0022."""
        return cls(
            agents={a.name: a for a in world.agents},
            scenarios={s.name: s for s in world.scenarios},
            models=world.models,
        )

    # ── building ───────────────────────────────────────────────────────────────

    def build_router(self) -> ModelRouter:
        """Construct a ModelRouter honouring the models config.

        ``models.offline: true`` forces the deterministic stub (the test/dev seam);
        ``false`` and the default (``null``) both build the live path.  On the live
        path live inference is **required**: if no backend is configured the build
        raises rather than silently degrading to the stub — the app has no offline
        product mode.
        """
        specs = {profile: ProfileSpec(**cfg.model_dump()) for profile, cfg in self.models.profiles.items()}
        if self.models.offline is True:
            return ModelRouter(offline=True, specs=specs)
        from src.models.openai_compat import has_live_credentials

        if not has_live_credentials():
            raise RuntimeError(
                "No inference backend configured. Set MODAL_WORKSPACE / MODAL_LLM_BASE_URL "
                "or HF_TOKEN / HF_INFERENCE_BASE_URL to run live, or set models.offline: true "
                "for the deterministic stub (tests/dev only)."
            )
        return ModelRouter(offline=False, specs=specs)

    def build_agent(self, name: str, router: ModelRouter, tools=None, memory_index=None) -> Agent:
        if name not in self.agents:
            raise KeyError(f"unknown agent {name!r} (have: {sorted(self.agents)})")
        manifest = self.agents[name]
        cls = HANDLERS.get(manifest.handler, ManifestAgent) if manifest.handler else ManifestAgent
        agent = cls(router, tools, memory_index)
        agent.manifest = manifest  # YAML is the source of truth for declarative fields
        return agent

    def build_scenario(self, name: str, router: ModelRouter | None = None, tools=None) -> Scenario:
        if name not in self.scenarios:
            raise KeyError(f"unknown scenario {name!r} (have: {sorted(self.scenarios)})")
        cfg = self.scenarios[name]
        router = router or self.build_router()
        # Optional semantic relevance index — env-gated (MEMORY_INDEX), a derived
        # lens over the ledger (ADR-0018).  None offline; one engine-wide index is
        # shared across the cast.
        from src.core.memory_index import memory_index_from_env

        memory_index = memory_index_from_env()
        agents = tuple(self.build_agent(agent_name, router, tools, memory_index) for agent_name in cfg.cast)
        obs.log(
            "registry.cast_assembled",
            scenario=cfg.name,
            cast=list(cfg.cast),
            count=len(cfg.cast),
            offline=getattr(router, "offline", None),
        )
        return Scenario(
            name=cfg.name,
            default_seed=cfg.default_seed,
            agents=agents,
            example_seeds=cfg.example_seeds,
            goal=cfg.goal,
            genesis_text=cfg.genesis_text,
            competition=cfg.competition,
        )

    def governor_for(self, name: str) -> Governor:
        """Build a Governor from a scenario's budget config (or defaults)."""
        cfg = self.scenarios.get(name)
        budget = (cfg.governor if cfg else None) or GovernorConfig()
        obs.log(
            "governor.configured",
            scenario=name,
            max_turns=budget.max_turns,
            max_calls_per_turn=budget.max_calls_per_turn,
            max_total_calls=budget.max_total_calls,
            max_total_tokens=budget.max_total_tokens,
            hourly_budget_usd=budget.hourly_budget_usd,
        )
        return Governor(
            max_turns=budget.max_turns,
            max_calls_per_turn=budget.max_calls_per_turn,
            max_total_calls=budget.max_total_calls,
            max_total_tokens=budget.max_total_tokens,
            hourly_budget_usd=budget.hourly_budget_usd,
        )


# ── module-level default ─────────────────────────────────────────────────────────

_default: Registry | None = None


def default_registry() -> Registry:
    """Lazily load (and cache) the repository's ``config/`` directory."""
    global _default
    if _default is None:
        _default = Registry.from_dir()
    return _default


# Load behaviour handlers so their @register_handler side effects run.  Imported
# at the bottom, after register_handler is defined, so there is no import cycle.
from src.agents import competition as _competition  # noqa: E402,F401
from src.agents import handlers as _handlers  # noqa: E402,F401
from src.agents import twenty_sprouts as _twenty_sprouts  # noqa: E402,F401
