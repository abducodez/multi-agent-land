"""THE STEEPED — the word-pair bluff scenario runs on the core engine (mock-free).

Exercises the spy game end-to-end through the real registry → Conductor → ledger path,
offline (deterministic stub, no API key):

  * the scenario + its cast load from ``config/`` and build into manifest agents;
  * players emit ``agent.spoke`` carrying the say-vs-think split (``thought`` + ``mood``);
  * the spy's private thoughts never leak its word into the public ``said`` line;
  * the ``spy-host`` handler attaches the unmasking ``reveal`` to its ``judge.verdict``.
"""

from __future__ import annotations

import re

from src.core.conductor import Conductor
from src.core.ledger_factory import make_ledger
from src.core.registry import default_registry

_PLAYERS = ("spy-cara", "spy-bex", "spy-nil", "spy-ovo")
_CAST = (*_PLAYERS, "spy-host")


def _run(steps: int = 4) -> Conductor:
    reg = default_registry()
    scn = reg.build_scenario("the-steeped")
    cond = Conductor(scn, governor=reg.governor_for("the-steeped"), ledger=make_ledger())
    cond.reset(scn.default_seed)
    for _ in range(steps):
        cond.step()
    return cond


def test_scenario_and_cast_load() -> None:
    reg = default_registry()
    assert "the-steeped" in reg.scenarios
    assert set(_CAST) <= set(reg.agents)
    scn = reg.scenarios["the-steeped"]
    assert list(scn.cast) == list(_CAST)


def test_players_emit_say_think_split() -> None:
    cond = _run()
    spoke = [e for e in cond.ledger.events if e.kind == "agent.spoke"]
    assert spoke, "expected the players to speak"
    assert {e.actor for e in spoke} <= set(_PLAYERS)
    # Every public utterance carries a private thought + a mood (the mind-reader split).
    for e in spoke:
        assert e.payload.get("text")
        assert e.payload.get("thought")
        assert e.payload.get("mood")


def test_secret_words_never_leak_into_public_lines() -> None:
    cond = _run()
    for e in cond.ledger.events:
        if e.kind == "agent.spoke":
            said = (e.payload.get("text") or "").upper()
            assert "COFFEE" not in said and "TEA" not in said


def test_public_setup_never_names_the_words() -> None:
    # The seed/goal (run.started) and the narrator's opening (world.observed) are
    # globally visible — every mind reads them. They must set up the game without
    # publishing the answer; the words live only in each persona + the host reveal.
    cond = _run()
    # Match the secret words as whole words, not substrings: run.started now carries the
    # competition block whose ``teams`` key contains the literal "TEA" inside "TEAMS"
    # (ADR-0029) — a coincidence, not a leak. The team map names the spy *agent*, never
    # the secret *word*, and run.started never reaches an agent's prompt (only `text`/
    # `goal` do). What must never appear is the word itself.
    leak = re.compile(r"\b(COFFEE|TEA)\b")
    for e in cond.ledger.events:
        if e.kind in ("run.started", "world.observed"):
            blob = str(e.payload).upper()
            assert not leak.search(blob), f"secret word leaked into {e.kind}: {e.payload}"


def test_host_verdict_unmasks_the_spy() -> None:
    cond = _run()
    verdicts = [e for e in cond.ledger.events if e.kind == "judge.verdict"]
    assert verdicts, "the host should deliver a verdict by turn 3"
    reveal = verdicts[-1].payload.get("reveal")
    assert isinstance(reveal, list) and reveal
    by_agent = {r["agent"]: r for r in reveal}
    assert by_agent["spy-nil"]["secret"] == "TEA"
    assert "SPY" in by_agent["spy-nil"]["role"]
    assert by_agent["spy-cara"]["secret"] == "COFFEE"
    assert by_agent["spy-cara"]["role"] == "HERD"
