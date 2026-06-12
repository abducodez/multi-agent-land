"""Verdict winner validation and the ground-truth scoreboard (ADR-0029).

The base agent validates a judge's ``winner`` (re-asking once on a bad pick, summing
usage, stamping ``no_contest`` on a second failure) and normalises ``scores`` in place.
The ``SpyHost`` handler then turns the judge's *accusation* into a code-stamped
*result* using the scenario's ``competition.teams``.

Zero mocks: agents are built through the real registry, and the live ``complete_structured``
seam is exercised with a small hand-written ``FakeProvider`` (the canonical zero-mock
seam) and an offline ``DeterministicTinyModel`` for the stub path.
"""

from __future__ import annotations

from src.agents.handlers import SpyHost
from src.core.events import Event
from src.core.projections import StageProjection
from src.core.registry import default_registry
from src.models.router import ModelRouter


# ── live-path provider seam (no unittest.mock) ────────────────────────────────────


class _ScriptedJudge:
    """A live provider whose ``complete_structured`` returns scripted verdicts.

    Exposing ``complete_structured`` is what routes the base agent down the live
    path (``hasattr(provider, "complete_structured")``).  Each call returns the next
    scripted ``(winner, scores)`` and reports the matching usage, so a re-ask is a
    real second round-trip the test can count and meter."""

    def __init__(self, scripts: list[dict]) -> None:
        self._scripts = scripts
        self.calls = 0
        self.last_usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        self.last_reasoning = ""
        self.model_id = "scripted"

    def complete_structured(self, role, prompt, model):
        script = self._scripts[min(self.calls, len(self._scripts) - 1)]
        self.calls += 1
        usage = script.get("usage", {"prompt_tokens": 100, "completion_tokens": 10, "total_tokens": 110})
        self.last_usage = dict(usage)
        return model(
            kind=script.get("kind", "judge.verdict"),
            text=script.get("text", "Verdict: someone did it."),
            mood=script.get("mood", "calm"),
            winner=script.get("winner"),
            scores=script.get("scores", {}),
        )


class _ScriptedRouter(ModelRouter):
    """A router that always hands back one scripted provider (the live seam)."""

    def __init__(self, provider) -> None:
        self._provider = provider
        self.offline = False

    def for_profile(self, key):  # type: ignore[override]
        return self._provider


def _judge(scripts: list[dict], *, scenario: str = "mystery-roots", name: str = "mystery-judge"):
    """Build a judge through the registry, wired to a scripted live provider."""
    reg = default_registry()
    provider = _ScriptedJudge(scripts)
    agent = reg.build_agent(name, _ScriptedRouter(provider))
    cfg = reg.scenarios[scenario]
    agent.competition = cfg.competition
    agent.cast_names = list(cfg.cast)
    return agent, provider


def _resolve(agent):
    return agent._resolve_payload(
        agent.manifest.name,
        "PROMPT",
        agent._content_kinds(),
        agent.manifest.output_extra_fields,
    )


# ── live re-ask flow ───────────────────────────────────────────────────────────────


class TestVerdictReask:
    def test_off_cast_winner_triggers_exactly_one_reask(self):
        # The reference flow: bad winner first, valid winner on the corrective re-ask.
        agent, provider = _judge(
            [
                {
                    "text": "Verdict: the butler did it.",
                    "winner": "NOT-A-CAST-NAME",
                    "scores": {"clue-gatherer": 12},
                    "usage": {"prompt_tokens": 100, "completion_tokens": 10, "total_tokens": 110},
                },
                {
                    "text": "Verdict: the gatherer cracked it.",
                    "winner": "clue-gatherer",
                    "scores": {"clue-gatherer": 9},
                    "usage": {"prompt_tokens": 120, "completion_tokens": 12, "total_tokens": 132},
                },
            ]
        )
        payload = _resolve(agent)
        assert provider.calls == 2  # one re-ask, not two, not zero
        assert payload["winner"] == "clue-gatherer"
        assert "no_contest" not in payload

    def test_reask_usage_is_summed_not_overwritten(self):
        # ADR-0029 acceptance criterion: the governor must meter BOTH calls.
        agent, _ = _judge(
            [
                {"winner": "bad", "usage": {"prompt_tokens": 100, "completion_tokens": 10, "total_tokens": 110}},
                {
                    "winner": "clue-gatherer",
                    "text": "Verdict: the gatherer cracked it.",
                    "usage": {"prompt_tokens": 120, "completion_tokens": 12, "total_tokens": 132},
                },
            ]
        )
        _resolve(agent)
        assert agent.last_usage["total_tokens"] == 242  # 110 + 132
        assert agent.last_usage["prompt_tokens"] == 220
        assert agent.last_usage["completion_tokens"] == 22

    def test_valid_first_try_does_not_reask(self):
        agent, provider = _judge([{"winner": "hypothesis-former", "text": "Verdict: the hypothesis held."}])
        payload = _resolve(agent)
        assert provider.calls == 1
        assert payload["winner"] == "hypothesis-former"
        assert "no_contest" not in payload

    def test_reask_that_also_fails_drops_winner_and_stamps_no_contest(self):
        # Two bad picks in a row: the verdict TEXT still ships, the row is forfeited.
        agent, provider = _judge(
            [
                {"winner": "bad-one", "text": "Verdict: I accuse the wind."},
                {"winner": "bad-two", "text": "Verdict: no, the rain."},
            ]
        )
        payload = _resolve(agent)
        assert provider.calls == 2
        assert payload.get("no_contest") is True
        assert "winner" not in payload
        assert payload["text"]  # the drama survives — the show always ends

    def test_missing_winner_is_not_an_error(self):
        # winner is optional; a judge that names none must NOT trigger a re-ask.
        agent, provider = _judge([{"winner": None, "text": "Verdict: the evidence is mute."}])
        payload = _resolve(agent)
        assert provider.calls == 1
        assert payload.get("winner") is None
        assert "no_contest" not in payload


class TestScoresNormalisation:
    """scores is garnish: cleaned in place, never re-asked."""

    def test_non_cast_keys_dropped_and_values_clamped(self):
        agent, provider = _judge(
            [
                {
                    "winner": "clue-gatherer",
                    "text": "Verdict: the gatherer cracked it.",
                    "scores": {"clue-gatherer": 12, "ghost": 5, "hypothesis-former": -3},
                }
            ]
        )
        payload = _resolve(agent)
        assert provider.calls == 1  # scores never cause a re-ask
        # ghost is off-cast → dropped; 12 → clamped to 10; -3 → clamped to 0.
        assert payload["scores"] == {"clue-gatherer": 10.0, "hypothesis-former": 0.0}

    def test_bad_winner_with_scores_still_only_reasks_for_winner(self):
        agent, provider = _judge(
            [
                {"winner": "bad", "scores": {"clue-gatherer": 99}},
                {
                    "winner": "clue-gatherer",
                    "text": "Verdict: the gatherer cracked it.",
                    "scores": {"clue-gatherer": 7},
                },
            ]
        )
        payload = _resolve(agent)
        assert provider.calls == 2
        assert payload["scores"] == {"clue-gatherer": 7.0}


class TestHookInert:
    """The validation hook is a transparent pass-through for everything that is not a
    judge in a live competition — even a downright odd winner slips through untouched."""

    def test_judged_winner_passes_unchanged(self):
        agent, provider = _judge([{"winner": "devils-advocate", "text": "Verdict: the advocate won."}])
        payload = _resolve(agent)
        assert provider.calls == 1
        assert payload["winner"] == "devils-advocate"

    def test_non_judge_role_leaves_odd_winner_untouched(self):
        # The hook keys on role == "judge".  Take the judge's exact schema (so the
        # winner field and verdict kind stay valid) but flip the role to a worker: the
        # hook must go inert, so an off-cast winner rides through verbatim — no
        # validation, no re-ask, no no_contest.
        reg = default_registry()
        provider = _ScriptedJudge([{"winner": "anything-goes", "text": "Verdict-shaped, but a worker said it."}])
        agent = reg.build_agent("mystery-judge", _ScriptedRouter(provider))
        cfg = reg.scenarios["mystery-roots"]
        agent.competition = cfg.competition
        agent.cast_names = list(cfg.cast)
        agent.manifest = agent.manifest.model_copy(update={"role": "worker"})
        payload = _resolve(agent)
        assert provider.calls == 1  # no re-ask despite the off-cast winner
        assert payload["winner"] == "anything-goes"
        assert "no_contest" not in payload

    def test_no_competition_attached_leaves_winner_untouched(self):
        # A bare-built judge (no registry injection) has competition=None → hook inert.
        reg = default_registry()
        provider = _ScriptedJudge([{"winner": "off-the-wall", "text": "Verdict: chaos reigns."}])
        agent = reg.build_agent("mystery-judge", _ScriptedRouter(provider))
        # Deliberately do NOT set agent.competition / cast_names.
        assert agent.competition is None
        payload = _resolve(agent)
        assert provider.calls == 1
        assert payload["winner"] == "off-the-wall"

    def test_none_kind_competition_is_inert(self):
        from src.core.config import CompetitionConfig

        reg = default_registry()
        provider = _ScriptedJudge([{"winner": "whoever", "text": "Verdict: nobody wins the wood."}])
        agent = reg.build_agent("mystery-judge", _ScriptedRouter(provider))
        agent.competition = CompetitionConfig(kind="none")
        agent.cast_names = ["clue-gatherer"]
        payload = _resolve(agent)
        assert provider.calls == 1
        assert payload["winner"] == "whoever"


# ── offline path: re-ask works there too, and the stub never triggers it ───────────


class TestOfflineVerdictPath:
    def test_offline_judge_emits_clean_winnerless_payload_no_reask(self):
        # The stub's _synth_field returns None/​{} for winner/scores, so an offline
        # judge produces a validation-clean payload with no wasted corrective round-trip.
        reg = default_registry()
        agent = reg.build_agent("mystery-judge", ModelRouter(offline=True))
        cfg = reg.scenarios["mystery-roots"]
        agent.competition = cfg.competition
        agent.cast_names = list(cfg.cast)
        payload = _resolve(agent)
        assert payload["winner"] is None
        assert payload["scores"] == {}
        assert "no_contest" not in payload


# ── SpyHost ground-truth scoreboard ────────────────────────────────────────────────


def _spy_host() -> SpyHost:
    reg = default_registry()
    host = reg.build_agent("spy-host", ModelRouter(offline=True))
    cfg = reg.scenarios["the-steeped"]
    host.competition = cfg.competition
    host.cast_names = list(cfg.cast)
    return host


class TestScanAccusation:
    """_scan_accusation recovers the accused from verdict text by the distinctive tail
    of each cast name (``spy-cara`` → ``cara``), earliest mention wins, host excluded."""

    def test_finds_named_player(self):
        host = _spy_host()
        assert host._scan_accusation("Verdict: I point at NIL.") == "spy-nil"

    def test_matches_case_insensitively(self):
        host = _spy_host()
        assert host._scan_accusation("CARA slipped a tell at dawn.") == "spy-cara"

    def test_earliest_mention_wins(self):
        host = _spy_host()
        # BEX appears before NIL → bex is the accusation, even though both are named.
        assert host._scan_accusation("BEX hesitated, then NIL steeped too fast.") == "spy-bex"

    def test_host_token_never_self_accuses(self):
        host = _spy_host()
        # The host's own name token ("host") is excluded; no player named → None.
        assert host._scan_accusation("The host weighs the room and the clues.") is None

    def test_no_recoverable_name_returns_none(self):
        host = _spy_host()
        assert host._scan_accusation("A long deliberation with no names at all.") is None


class TestStampScoreboard:
    """The load-bearing split: code turns the judge's accusation into the result."""

    def _verdict_event(self, **payload) -> Event:
        return Event(run_id="r", turn=1, kind="judge.verdict", actor="spy-host", payload=payload)

    def test_correct_accusation_lets_the_herd_win(self):
        host = _spy_host()
        event = self._verdict_event(text="Verdict: NIL is the spy.", winner="spy-nil")
        host._stamp_scoreboard(event)
        assert event.payload["accused"] == "spy-nil"
        assert event.payload["correct"] is True
        assert event.payload["winner"] == "herd"  # spy caught → herd wins

    def test_wrong_accusation_lets_the_spy_win(self):
        host = _spy_host()
        event = self._verdict_event(text="Verdict: CARA is the spy.", winner="spy-cara")
        host._stamp_scoreboard(event)
        assert event.payload["accused"] == "spy-cara"
        assert event.payload["correct"] is False
        assert event.payload["winner"] == "spy"  # innocent accused → spy wins

    def test_offline_accusation_recovered_from_text(self):
        # No winner field (the offline shape) → the accusation is scanned from text.
        host = _spy_host()
        event = self._verdict_event(text="Verdict: I point at NIL. The herd's clues brewed.")
        host._stamp_scoreboard(event)
        assert event.payload["accused"] == "spy-nil"
        assert event.payload["winner"] == "herd"

    def test_no_recoverable_accusation_is_no_contest(self):
        host = _spy_host()
        event = self._verdict_event(text="A long deliberation with no names at all.")
        host._stamp_scoreboard(event)
        assert event.payload.get("no_contest") is True
        assert "winner" not in event.payload

    def test_no_spy_team_is_inert(self):
        # A versus competition without a 'spy' team has no ground truth to stamp.
        from src.core.config import CompetitionConfig

        host = _spy_host()
        host.competition = CompetitionConfig(kind="versus", teams={"a": ["spy-cara"], "b": ["spy-bex"]})
        event = self._verdict_event(text="Verdict: NIL.", winner="spy-nil")
        host._stamp_scoreboard(event)
        # Untouched: no accused/correct/no_contest stamped, winner left as the raw pick.
        assert "accused" not in event.payload
        assert "correct" not in event.payload
        assert event.payload["winner"] == "spy-nil"


class TestSpyHostActEnrichment:
    """The full handler turn: super().act() produces the verdict, then the scoreboard
    is stamped — driven entirely offline through the deterministic stub."""

    def test_act_stamps_a_full_scoreboard_offline(self):
        host = _spy_host()
        # The stub's spy-host lines all name NIL → a deterministic herd win.
        recent = tuple(
            Event(run_id="r", turn=1, kind="agent.spoke", actor=p, payload={"text": "a clue"})
            for p in ("spy-cara", "spy-bex", "spy-nil", "spy-ovo")
        )
        projection = StageProjection()
        event = host.act("r", 2, projection, recent)
        assert event.kind == "judge.verdict"
        assert event.payload["accused"] == "spy-nil"
        assert event.payload["correct"] is True
        assert event.payload["winner"] == "herd"
        # The dramatic reveal still rides alongside the scoreboard.
        assert isinstance(event.payload.get("reveal"), list) and event.payload["reveal"]
