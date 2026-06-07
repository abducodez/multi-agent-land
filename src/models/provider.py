from __future__ import annotations

import hashlib
from dataclasses import dataclass


class ModelProvider:
    def complete(self, role: str, prompt: str) -> str:
        raise NotImplementedError


@dataclass
class DeterministicTinyModel(ModelProvider):
    """Local deterministic stand-in until small hosted models are wired in."""

    variant: str = "stub<=4b"

    def complete(self, role: str, prompt: str) -> str:
        digest = hashlib.sha256(f"{role}:{prompt}".encode("utf-8")).hexdigest()
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
        return options[int(digest[:2], 16) % len(options)]

