"""In-process transformers provider — the local-GPU transport for the ``local`` backend.

This is the *serving* side of the local backend whose catalogue lives in
:mod:`src.models.local_catalogue`. Where :class:`~src.models.litellm_provider.LiteLLMProvider`
calls a model over an OpenAI-compatible HTTP endpoint, this provider runs a small
``transformers`` model **in the same process, on the host's own GPU**, behind a
``@spaces.GPU`` function — so a Hugging Face Space serves the cast on its own hardware
with no endpoint to deploy and no token to hold.

It is hardware-agnostic (ADR-0033). ``@spaces.GPU`` is **effect-free off ZeroGPU**, so the
one decorated ``_generate`` covers every flavour:

  * **ZeroGPU** — the decorator allocates a GPU for the call and releases it after.
  * **Dedicated GPU / local CUDA** — the decorator is a passthrough; the model runs on
    the persistent GPU.

**Where the weights load vs. where CUDA is touched.** ZeroGPU grants a real GPU *only*
for the duration of a ``@spaces.GPU`` call (each call forks a worker); the parent process
never gets one, and any low-level CUDA init outside such a call — including a lazy
``.to("cuda")`` at request time, which ZeroGPU's startup hook does not capture — trips the
fork guard and kills the process. So the split is: :meth:`complete` warms a module-level
**CPU** cache in the parent first (lazily, on first use — never at app boot, never on
CUDA), and the decorated ``_generate`` moves those weights onto the GPU **inside the
granted window** and runs the forward pass. The forked worker inherits the parent's CPU
weights, so it pays a host→device copy, not a disk reload; on a dedicated GPU the cached
module simply stays resident across calls (the move is a no-op after the first).

Heavy imports (``torch`` / ``transformers``) are lazy — confined to the functions that
need them — so importing this module never initialises CUDA (which would trip ZeroGPU's
fork guard) and the offline path never pays for them. ``spaces`` itself is import-safe
everywhere. ``complete`` returns the failure sentinel on any error (never raises), exactly
like the HTTP provider, so the conductor's resilient loop treats a local-inference hiccup
the same as a flaky endpoint.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import spaces  # import-safe everywhere (effect-free off ZeroGPU); needed for @spaces.GPU

from src import observability as obs
from src.models.openai_compat import OpenAICompatProvider
from src.models.provider import ModelProvider, estimate_tokens, model_error

# Loaded models, keyed by repo id: ``repo_id -> (tokenizer, model)``. Populated in the
# *parent* process by :func:`_ensure_loaded` so each forked ``@spaces.GPU`` call inherits
# the weights instead of reloading them (see the module docstring). Module-level so the
# cache survives across provider instances and across ticks of a show.
_LOADED: dict[str, tuple] = {}


def _ensure_loaded(repo_id: str, trust_remote_code: bool) -> tuple:
    """Load (once, cached) the tokenizer + model for *repo_id* **on CPU**.

    Called from :meth:`LocalTransformersProvider.complete` in the parent process to warm
    the weights in host RAM. It deliberately **never touches CUDA**: under ZeroGPU the
    parent process gets no GPU, and any low-level CUDA init outside a ``@spaces.GPU`` call
    (a lazy ``.to("cuda")`` at request time is not captured by ZeroGPU's startup hook)
    trips the fork guard and kills the process. The CPU→GPU transfer happens inside the
    decorated :func:`_generate`, where a real GPU is granted; the forked worker inherits
    these CPU weights, so it pays a device copy, not a disk reload. ``dtype="auto"`` lets
    transformers pick the weights' native precision (fallback to the legacy ``torch_dtype``
    kwarg name on older transformers).
    """
    if repo_id in _LOADED:
        return _LOADED[repo_id]
    from transformers import AutoModelForCausalLM, AutoTokenizer

    tokenizer = AutoTokenizer.from_pretrained(repo_id, trust_remote_code=trust_remote_code)
    try:
        model = AutoModelForCausalLM.from_pretrained(repo_id, dtype="auto", trust_remote_code=trust_remote_code)
    except TypeError:  # pragma: no cover - older transformers use the torch_dtype kwarg name
        model = AutoModelForCausalLM.from_pretrained(repo_id, torch_dtype="auto", trust_remote_code=trust_remote_code)
    model.eval()
    _LOADED[repo_id] = (tokenizer, model)
    return _LOADED[repo_id]


def _gpu_duration(repo_id, trust_remote_code, system, prompt, max_new_tokens, temperature, top_p) -> int:
    """Dynamic ``@spaces.GPU`` duration (seconds) for one generation.

    Scales with the token budget and stays short so the Space keeps high queue priority on
    ZeroGPU (shorter declared durations are prioritised). The weights are already warm in
    the parent, so this only needs to cover the forward pass, not a model load.
    """
    return min(120, 20 + int(max_new_tokens) // 4)


@spaces.GPU(duration=_gpu_duration)
def _generate(repo_id, trust_remote_code, system, prompt, max_new_tokens, temperature, top_p):
    """Run one chat completion on the GPU; return ``(text, prompt_tokens, completion_tokens)``.

    Module-level and decorated so ZeroGPU registers it and grants a GPU for the call. The
    model weights are fetched from the parent-warmed CPU cache (a hit — never a disk reload
    here) and moved onto the device **inside this GPU window** — the only place ZeroGPU
    permits CUDA init. On ZeroGPU the forked worker inherits CPU weights and pays one
    device copy per call; on a dedicated GPU the cached module is already resident after
    the first call, so the move is a no-op. Input tensors are built and placed on the same
    device.
    """
    import torch

    tokenizer, model = _ensure_loaded(repo_id, trust_remote_code)
    if torch.cuda.is_available():
        model = model.to("cuda")
    device = next(model.parameters()).device
    messages = [{"role": "system", "content": system}, {"role": "user", "content": prompt}]
    inputs = tokenizer.apply_chat_template(messages, add_generation_prompt=True, return_tensors="pt").to(device)
    do_sample = temperature and float(temperature) > 0
    with torch.no_grad():
        output = model.generate(
            inputs,
            max_new_tokens=int(max_new_tokens),
            do_sample=bool(do_sample),
            temperature=float(temperature) if do_sample else None,
            top_p=float(top_p) if do_sample else None,
            pad_token_id=tokenizer.pad_token_id if tokenizer.pad_token_id is not None else tokenizer.eos_token_id,
        )
    generated = output[0][inputs.shape[-1] :]
    text = tokenizer.decode(generated, skip_special_tokens=True).strip()
    return text, int(inputs.shape[-1]), int(generated.shape[-1])


@dataclass
class LocalTransformersProvider(ModelProvider):
    """Serve one logical profile by running a ``transformers`` model on the host GPU.

    ``model`` is the bare ``transformers`` repo id (e.g. ``"Qwen/Qwen2.5-3B-Instruct"``) —
    the same string :func:`src.models.local_catalogue.binding_for` returns. Decoding
    (``temperature`` / ``top_p`` / ``max_tokens``) comes from the router's per-profile
    spec. ``trust_remote_code`` is resolved from the catalogue for the repo (default
    ``False`` for an off-catalogue id).
    """

    model: str
    temperature: float = 0.7
    top_p: float = 0.95
    max_tokens: int = 256
    _last_usage: dict = field(default_factory=dict, init=False, repr=False)

    def complete(self, role: str, prompt: str) -> str:
        span_attrs = {
            "gen_ai.system": "transformers-local",
            "gen_ai.request.model": self.model,
            "gen_ai.request.temperature": self.temperature,
            "gen_ai.request.max_tokens": self.max_tokens,
            "mal.role": role,
        }
        with obs.span("llm.call", **span_attrs):
            try:
                # Warm the weights in the PARENT first so the forked @spaces.GPU call
                # inherits them (see module docstring); this is a cache hit after the
                # first use of this model in the process.
                _ensure_loaded(self.model, self._trust_remote_code())
                system = OpenAICompatProvider._system_for_role(role)
                text, prompt_tokens, completion_tokens = _generate(
                    self.model,
                    self._trust_remote_code(),
                    system,
                    prompt,
                    self.max_tokens,
                    self.temperature,
                    self.top_p,
                )
                self._record_usage(prompt_tokens, completion_tokens, prompt, text)
                self._emit_telemetry(role, prompt, text)
                return text
            except Exception as exc:
                self._zero_usage()
                obs.log("llm.error", level="warning", model=self.model, role=role, error=str(exc))
                return model_error(exc)

    # ── internals ───────────────────────────────────────────────────────────────

    def _trust_remote_code(self) -> bool:
        """Whether the catalogue marks this repo as needing custom modelling code.

        Looked up by repo id; an id not in the catalogue (a hand-pinned repo) defaults to
        ``False`` — the safe choice, and the Lab only ever offers catalogue models.
        """
        from src.models import local_catalogue

        entry = local_catalogue.model_by_key(self.model)
        return bool(entry.trust_remote_code) if entry is not None else False

    def _record_usage(self, prompt_tokens: int, completion_tokens: int, prompt: str, text: str) -> None:
        # Generation returns exact token counts; fall back to an estimate only if a count
        # came back as zero (e.g. an empty decode), so the Governor always sees a budget hit.
        prompt_tokens = int(prompt_tokens) or estimate_tokens(prompt)
        completion_tokens = int(completion_tokens) or estimate_tokens(text)
        self._last_usage = {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        }

    def _zero_usage(self) -> None:
        self._last_usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}

    def _emit_telemetry(self, role: str, prompt: str, text: str) -> None:
        usage = self._last_usage or {}
        prompt_tokens = int(usage.get("prompt_tokens", 0) or 0)
        completion_tokens = int(usage.get("completion_tokens", 0) or 0)
        obs.add_span_attrs(
            **{
                "gen_ai.usage.input_tokens": prompt_tokens,
                "gen_ai.usage.output_tokens": completion_tokens,
                "llm.cost_usd": 0.0,  # local inference has no per-call price (GPU is the cost)
                "llm.structured": False,
                "llm.prompt": prompt,
                "llm.completion": text,
            }
        )
        obs.record_llm_call(self.model, prompt_tokens=prompt_tokens, completion_tokens=completion_tokens, cost_usd=0.0)
        obs.log(
            "llm.call",
            role=role,
            model=self.model,
            structured=False,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cost_usd=0.0,
        )
        obs.log("llm.exchange", level="debug", role=role, model=self.model, prompt=prompt, completion=text)
