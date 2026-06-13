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

**Two phases, split across the ZeroGPU fork.** ZeroGPU grants a real GPU *only* for the
duration of a ``@spaces.GPU`` call (each call runs in a forked worker); the parent process
never gets one, and any low-level CUDA init outside such a call kills the process. So the
work is split:

  * **Parent — download only, never CUDA** (:func:`_ensure_downloaded`): fetch the repo's
    weights to the on-disk HF cache with ``snapshot_download``. This pays the network cost
    once, in the resilient parent, so the short GPU window never spends its budget pulling
    gigabytes. It deliberately does **not** materialise the model in host RAM — a cast of
    four 8–12B models would otherwise pin ~60GB of parent RAM for the whole show.
  * **Worker — load straight onto the GPU** (:func:`_ensure_loaded_on_device`): inside the
    granted window, ``from_pretrained(device_map={"": 0}, local_files_only=True)`` lets
    transformers + accelerate **materialise and place** every weight, tied head, and
    non-persistent buffer directly on the device in one atomic step, then caches the
    device-resident model per repo (a reused worker — and any dedicated GPU — keeps it
    resident across calls).

**Why ``device_map`` and not a manual ``.to("cuda")``.** transformers 5.x always builds the
model on the ``meta`` device and streams the checkpoint onto the target. A bare
``from_pretrained(...).to("cuda")`` leaves a model whose non-persistent buffers (e.g. a
rotary ``inv_freq``) or a tied/"missing" head can still sit on ``meta``, and the later
``.to("cuda")`` then dies with *"Cannot copy out of meta tensor; no data!"*
(transformers#41038/#30703) — and ``low_cpu_mem_usage`` no longer changes this (5.x drops
the kwarg outright). Handing transformers the device via ``device_map`` is the supported
path: ``_move_missing_keys_from_meta_to_device`` places the buffers and missing keys on the
mapped device and ``initialize_weights``/``tie_weights`` run there, so **nothing is ever
left on meta** and there is no fragile post-hoc move. This needs ``accelerate`` (a declared
dep); the kwarg-only fallbacks keep older transformers working.

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

# Device-resident models, keyed by repo id: ``repo_id -> (tokenizer, model)``. Populated in
# the forked ``@spaces.GPU`` worker by :func:`_ensure_loaded_on_device`, so a reused worker
# (and any dedicated GPU) keeps the model resident across calls. Module-level so the cache
# survives across provider instances and across ticks of a show.
_LOADED: dict[str, tuple] = {}

# Repo ids whose weights have been fetched to the on-disk HF cache by :func:`_ensure_downloaded`
# in the *parent*. A set, not a model cache — the parent holds no weights in RAM (see the
# module docstring); it only records "this repo is on disk" so we skip the network re-check.
_DOWNLOADED: set[str] = set()


def _always_true(*_args, **_kwargs) -> bool:
    return True


# v4-era capability predicates that transformers 5.x removed but Hub ``trust_remote_code``
# modelling files still import (e.g. MiniCPM's modeling_minicpm.py does
# ``from transformers.utils.import_utils import is_torch_fx_available``). All of these are
# unconditionally True at this project's torch>=2.8 floor — exactly the value the
# transformers maintainers say is now correct (transformers#44561) — so back-filling them
# lets such remote code import instead of crashing with ``cannot import name '…'``.
_REMOVED_TORCH_PREDICATES = ("is_torch_fx_available", "is_torch_sdpa_available")


def _ensure_transformers_v4_symbols() -> None:
    """Restore removed v4-era predicates onto ``transformers.utils`` so older Hub remote
    code (loaded via ``trust_remote_code``) imports cleanly. Idempotent — only fills a name
    that is genuinely absent, so it never shadows a function transformers still ships."""
    try:
        import transformers.utils as tu
        from transformers.utils import import_utils
    except Exception:  # pragma: no cover - transformers absent → offline path, nothing to do
        return
    for mod in (import_utils, tu):
        for name in _REMOVED_TORCH_PREDICATES:
            if not hasattr(mod, name):
                setattr(mod, name, _always_true)


def _ensure_downloaded(repo_id: str, trust_remote_code: bool) -> None:
    """Fetch *repo_id*'s files to the on-disk HF cache **in the parent**, without CUDA.

    Called from :meth:`LocalTransformersProvider.complete` in the parent process. It pulls
    the weights (and, for ``trust_remote_code`` repos, the modelling ``.py`` files) over the
    network *once*, so the later ``@spaces.GPU`` window — where the GPU budget is scarce —
    loads from a warm local cache instead of downloading. It deliberately **never touches
    CUDA** (under ZeroGPU the parent gets no GPU) and **never materialises the model** in
    host RAM: a multi-model cast would otherwise pin tens of GB of parent RAM for the whole
    show. Cached via :data:`_DOWNLOADED` so repeated turns skip the network revision check.

    Errors propagate to :meth:`complete`, which turns them into the resilient failure
    sentinel — and crucially we never spend a GPU window on a model whose weights are
    missing.
    """
    if repo_id in _DOWNLOADED:
        return
    from huggingface_hub import snapshot_download

    # Honour the same gate transformers does (HF_TOKEN for gated repos like Aya); the Space
    # sets it in the environment. snapshot_download is a no-op-ish revision check once cached.
    snapshot_download(repo_id)
    _DOWNLOADED.add(repo_id)


def _ensure_loaded_on_device(repo_id: str, trust_remote_code: bool) -> tuple:
    """Load (once, cached) the tokenizer + model **directly onto the GPU** for *repo_id*.

    Runs inside the decorated :func:`_generate`, where ZeroGPU has granted a real device.
    ``device_map={"": 0}`` hands transformers the placement so it **materialises and places**
    every weight, tied head and non-persistent buffer on the device in one step — the
    supported path that leaves nothing on the ``meta`` device for a later move to choke on
    (see the module docstring). ``local_files_only=True`` keeps the GPU window off the
    network: the parent already fetched the repo (:func:`_ensure_downloaded`), so a missing
    file fails fast here rather than burning the budget on a download. ``dtype="auto"`` keeps
    the checkpoint's native precision (falling back to the legacy ``torch_dtype`` kwarg name
    on older transformers).

    Off a GPU (a misconfigured call — :func:`_generate` is normally gated behind a device)
    it degrades to a plain CPU load so the provider still answers rather than crashing.
    """
    if repo_id in _LOADED:
        return _LOADED[repo_id]
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer

    # Back-fill v4-era symbols removed in transformers 5.x before any trust_remote_code
    # modelling file is imported (tokenizer or model), or it crashes at import time.
    _ensure_transformers_v4_symbols()
    tokenizer = AutoTokenizer.from_pretrained(repo_id, trust_remote_code=trust_remote_code, local_files_only=True)
    # device_map places the model on the granted GPU; CPU is the degenerate off-GPU fallback.
    device_map = {"": 0} if torch.cuda.is_available() else {"": "cpu"}
    load_kwargs = dict(device_map=device_map, trust_remote_code=trust_remote_code, local_files_only=True)
    try:
        model = AutoModelForCausalLM.from_pretrained(repo_id, dtype="auto", **load_kwargs)
    except TypeError:  # pragma: no cover - older transformers use the torch_dtype kwarg name
        model = AutoModelForCausalLM.from_pretrained(repo_id, torch_dtype="auto", **load_kwargs)
    model.eval()
    _LOADED[repo_id] = (tokenizer, model)
    return _LOADED[repo_id]


def _gpu_duration(repo_id, trust_remote_code, use_cache, system, prompt, max_new_tokens, temperature, top_p) -> int:
    """Dynamic ``@spaces.GPU`` duration (seconds) for one generation.

    Scales with the token budget and stays bounded so the Space keeps reasonable queue
    priority on ZeroGPU (shorter declared durations are prioritised). The base covers a cold
    device load: the first call for a model in a freshly forked worker materialises the
    weights onto the GPU (from the parent-warmed disk cache), and that must finish inside the
    granted window. Subsequent calls hit the resident cache and use only the forward-pass tail.
    """
    return min(120, 60 + int(max_new_tokens) // 4)


@spaces.GPU(duration=_gpu_duration)
def _generate(repo_id, trust_remote_code, use_cache, system, prompt, max_new_tokens, temperature, top_p):
    """Run one chat completion on the GPU; return ``(text, prompt_tokens, completion_tokens)``.

    Module-level and decorated so ZeroGPU registers it and grants a GPU for the call. The
    model is loaded straight onto the device via :func:`_ensure_loaded_on_device` (cached
    per repo — a disk→device materialise on first use, a no-op on later calls), so the
    forward pass runs entirely on the granted GPU with no post-hoc device move. Input tensors
    are built and placed on the model's own device.
    """
    import torch

    tokenizer, model = _ensure_loaded_on_device(repo_id, trust_remote_code)
    device = next(model.parameters()).device
    messages = [{"role": "system", "content": system}, {"role": "user", "content": prompt}]
    # return_dict=True yields a BatchEncoding (input_ids + attention_mask). This is the
    # default in transformers 5.x and we request it explicitly so the call is robust across
    # versions: a bare-tensor return (older default) would be passed positionally into
    # generate() as `inputs`, and 5.x's generate() then does inputs.shape[0] on the dict →
    # AttributeError. Unpacking with ** feeds input_ids AND the attention mask correctly.
    inputs = tokenizer.apply_chat_template(
        messages, add_generation_prompt=True, return_tensors="pt", return_dict=True
    ).to(device)
    input_len = int(inputs["input_ids"].shape[-1])
    do_sample = temperature and float(temperature) > 0
    with torch.no_grad():
        output = model.generate(
            **inputs,
            max_new_tokens=int(max_new_tokens),
            do_sample=bool(do_sample),
            temperature=float(temperature) if do_sample else None,
            top_p=float(top_p) if do_sample else None,
            # Per-model: False for repos whose custom code mishandles the 5.x KV cache.
            use_cache=bool(use_cache),
            pad_token_id=tokenizer.pad_token_id if tokenizer.pad_token_id is not None else tokenizer.eos_token_id,
        )
    generated = output[0][input_len:]
    text = tokenizer.decode(generated, skip_special_tokens=True).strip()
    return text, input_len, int(generated.shape[-1])


@dataclass
class LocalTransformersProvider(ModelProvider):
    """Serve one logical profile by running a ``transformers`` model on the host GPU.

    ``model`` is the bare ``transformers`` repo id (e.g. ``"openbmb/MiniCPM5-1B"``) —
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
                # Fetch the weights to disk in the PARENT (no CUDA, no RAM materialise) so the
                # forked @spaces.GPU call below loads from a warm cache (see module docstring).
                _ensure_downloaded(self.model, self._trust_remote_code())
                system = OpenAICompatProvider._system_for_role(role)
                text, prompt_tokens, completion_tokens = _generate(
                    self.model,
                    self._trust_remote_code(),
                    self._use_cache(),
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

    def _use_cache(self) -> bool:
        """Whether to use the generation KV cache for this repo (from the catalogue).

        Defaults to True (the fast path); the catalogue can set it False for a custom-code
        repo whose attention mishandles transformers 5.x's cache API. The current cast is
        all native-arch so none do; an off-catalogue id likewise keeps the cache on.
        """
        from src.models import local_catalogue

        entry = local_catalogue.model_by_key(self.model)
        return bool(entry.use_cache) if entry is not None else True

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
