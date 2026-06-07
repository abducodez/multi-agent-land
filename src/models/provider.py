from __future__ import annotations

import hashlib
from dataclasses import dataclass, field


def estimate_tokens(text: str) -> int:
    """Rough token estimate (~4 chars/token) for providers without usage data.

    Used by the deterministic stub and as a fallback when an endpoint does not
    return a usage block.  Good enough to feed the Governor's token budget.
    """
    return max(1, len(text) // 4)


class ModelProvider:
    def complete(self, role: str, prompt: str) -> str:
        raise NotImplementedError

    @property
    def last_usage(self) -> dict[str, int]:
        """Token usage of the most recent complete() call.

        Subclasses set ``self._last_usage``.  Defaults to zeros so callers can
        always read ``provider.last_usage`` without a hasattr guard.
        """
        return getattr(
            self,
            "_last_usage",
            {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        )


@dataclass
class DeterministicTinyModel(ModelProvider):
    """Local deterministic stand-in until small hosted models are wired in.

    Serves every model profile offline so demos and tests are fully reproducible
    without an API key.  The ``variant`` (e.g. ``"stub:tiny"``) is folded into the
    hash so different profiles can produce different lines from the same prompt.
    """

    variant: str = "stub<=4b"
    _last_usage: dict[str, int] = field(default_factory=dict, init=False, repr=False)

    def complete(self, role: str, prompt: str) -> str:
        digest = hashlib.sha256(f"{self.variant}:{role}:{prompt}".encode("utf-8")).hexdigest()
        choices = {
            "scene-whisperer": [
                "A mossy ticket booth opens in a tree root and sells yesterday's dreams for acorns.",
                "The path folds itself into a paper crane and refuses to point north.",
                "Every mushroom cap becomes a tiny stage light, waiting for a secret cue.",
            ],
            "mischief-critic": [
                "Verdict: keep it. The image is specific, playable, and invites the next agent to react.",
                "Verdict: raise the stakes. The scene needs a want, a rule, or a tiny consequence.",
                "Verdict: delightful but thin. Add a choice the visitor can disturb.",
            ],
            "pocket-actor": [
                "I am collecting echoes so I can knit a ladder to the moon.",
                "Please do not applaud yet; my shadow is still rehearsing.",
                "I lost the map, but the map keeps sending postcards.",
            ],
        }
        options = choices.get(role, ["The wood hums and waits."])
        out = options[int(digest[:2], 16) % len(options)]
        self._last_usage = {
            "prompt_tokens": estimate_tokens(prompt),
            "completion_tokens": estimate_tokens(out),
            "total_tokens": estimate_tokens(prompt) + estimate_tokens(out),
        }
        return out
