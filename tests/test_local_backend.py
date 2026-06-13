"""Tests for the local in-process backend — catalogue, gate, registry, router dispatch.

The ``local`` backend (ADR-0033) runs a small ``transformers`` model in-process on the
host GPU behind ``@spaces.GPU`` — hardware-agnostic (ZeroGPU or a dedicated GPU), with no
HTTP endpoint. These tests cover the deterministic, offline-safe surface: the catalogue
data, the capability gate (env signals + an injectable CUDA probe), the unified-registry
integration, and that the router dispatches a ``local:`` key to the in-process provider
rather than the HTTP gateway. The actual GPU forward pass is integration-only (it needs a
GPU and weights), exactly as the HTTP provider's live call is — so nothing here downloads
a model or touches CUDA.
"""

from __future__ import annotations

import os

import pytest

from src.models import inference, local_catalogue
from src.models.local_provider import LocalTransformersProvider
from src.models.router import ModelRouter


# ── catalogue ─────────────────────────────────────────────────────────────────────


def test_one_sponsor_model_per_tier_and_sizes_stay_small():
    # Each tier maps to a *distinct* sponsor model (the multi-track cast), so one show spans
    # NVIDIA · OpenBMB · Cohere · JetBrains. Every model honours the ≤32B rule and the tiny
    # default keeps the Tiny-Titan ≤4B band.
    tagged = {m.profile: m for m in local_catalogue.LOCAL_MODELS if m.profile is not None}
    assert set(tagged) == {"tiny", "fast", "balanced", "strong"}
    assert all(m.params_b is None or m.params_b <= 32 for m in local_catalogue.LOCAL_MODELS)
    assert tagged["tiny"].params_b <= 4  # Tiny-Titan band
    assert len({m.source for m in tagged.values()}) == 4  # four sponsor families


def test_every_tier_resolves_to_its_sponsor_model():
    assert local_catalogue.default_key_for_profile("tiny") == "nvidia/Nemotron-Mini-4B-Instruct"
    assert local_catalogue.default_key_for_profile("fast") == "openbmb/MiniCPM4.1-8B"
    assert local_catalogue.default_key_for_profile("balanced") == "CohereLabs/aya-expanse-8b"
    assert local_catalogue.default_key_for_profile("strong") == "JetBrains/Mellum2-12B-A2.5B-Instruct"
    # the tiny model is listed first, so an untagged/unknown tier falls back to the cheapest.
    assert local_catalogue.LOCAL_MODELS[0].profile == "tiny"


def test_model_by_key_carries_trust_remote_code():
    # MiniCPM ships custom modelling code; the native-arch models (Nemotron-Mini, Aya) do
    # not; an off-catalogue id is unknown.
    assert local_catalogue.model_by_key("openbmb/MiniCPM4.1-8B").trust_remote_code is True
    assert local_catalogue.model_by_key("nvidia/Nemotron-Mini-4B-Instruct").trust_remote_code is False
    assert local_catalogue.model_by_key("CohereLabs/aya-expanse-8b").trust_remote_code is False
    assert local_catalogue.model_by_key("does/not-exist") is None


def test_binding_is_a_bare_repo_id_with_no_endpoint():
    # In-process: the binding carries the raw transformers repo id (no openai/ prefix) and
    # neither a base_url nor an api_key — the router builds the in-process provider from it.
    binding = local_catalogue.binding_for("nvidia/Nemotron-Mini-4B-Instruct")
    assert binding["model"] == "nvidia/Nemotron-Mini-4B-Instruct"
    assert binding["base_url"] == ""
    assert binding["api_key"] == ""


def test_binding_unknown_key_raises():
    with pytest.raises(KeyError):
        local_catalogue.binding_for("nobody/here")


# ── capability gate ─────────────────────────────────────────────────────────────────


def test_gate_explicit_env_is_deterministic_without_a_probe():
    # An explicit env dict is the whole story — no torch import, no host probe.
    assert local_catalogue.has_credentials(env={}) is False
    assert local_catalogue.has_credentials(env={"SPACES_ZERO_GPU": "true"}) is True
    assert local_catalogue.has_credentials(env={"LOCAL_INFERENCE": "1"}) is True


def test_gate_accepts_common_truthy_spellings():
    for val in ("1", "true", "TRUE", "yes", "on"):
        assert local_catalogue.has_credentials(env={"LOCAL_INFERENCE": val}) is True
    for val in ("0", "false", "", "no"):
        assert local_catalogue.has_credentials(env={"LOCAL_INFERENCE": val}) is False


def test_gate_uses_injected_cuda_probe_when_env_signals_absent():
    # No env signal → fall through to the probe (auto-detect a dedicated GPU / local box).
    assert local_catalogue.has_credentials(env={}, cuda_probe=lambda: True) is True
    assert local_catalogue.has_credentials(env={}, cuda_probe=lambda: False) is False
    # An env signal short-circuits before the probe is ever consulted.
    assert local_catalogue.has_credentials(env={"SPACES_ZERO_GPU": "1"}, cuda_probe=lambda: False) is True


def test_gate_auto_probes_only_against_the_real_environment():
    # Passing os.environ itself opts into the host CUDA probe; an arbitrary dict does not,
    # keeping façade/test calls deterministic. We assert the boolean, whatever the host is.
    assert isinstance(local_catalogue.has_credentials(env=os.environ), bool)


# ── unified registry integration ─────────────────────────────────────────────────────


def test_local_backend_is_registered_and_qualified():
    assert "local" in {b.key for b in inference.backends()}
    keys = {e["key"] for e in inference.entries("local")}
    assert keys and all(k.startswith("local:") for k in keys)


def test_registry_default_and_binding_round_trip():
    key = inference.default_key_for_profile("tiny", "local")
    assert key == "local:nvidia/Nemotron-Mini-4B-Instruct"
    binding = inference.binding_for(key)
    assert binding["model"] == "nvidia/Nemotron-Mini-4B-Instruct"
    assert binding["base_url"] == ""


def test_backend_available_and_configured_backends_for_local():
    assert inference.backend_available("local", env={"LOCAL_INFERENCE": "1"}) is True
    assert inference.backend_available("local", env={"SPACES_ZERO_GPU": "yes"}) is True
    assert inference.backend_available("local", env={}) is False
    configured = inference.configured_backends(env={"LOCAL_INFERENCE": "1"})
    assert "local" in configured


# ── router dispatch ──────────────────────────────────────────────────────────────────


def test_router_dispatches_local_key_to_in_process_provider():
    # A live router resolving a local: key must build the in-process provider (not LiteLLM),
    # bound to the bare repo id. Construction only — no GPU is touched.
    router = ModelRouter(offline=False)
    provider = router.for_profile("local:nvidia/Nemotron-Mini-4B-Instruct")
    assert isinstance(provider, LocalTransformersProvider)
    assert provider.model == "nvidia/Nemotron-Mini-4B-Instruct"
    assert provider.model_id == "nvidia/Nemotron-Mini-4B-Instruct"


def test_catalogue_spec_tags_local_kind_and_others_litellm():
    router = ModelRouter(offline=False)
    local_spec = router._catalogue_spec("local:nvidia/Nemotron-Mini-4B-Instruct")
    assert local_spec is not None and local_spec.kind == "local"
    # An HF key resolves through the same path but stays on the HTTP transport.
    hf_spec = router._catalogue_spec("hf:katanemo/Arch-Router-1.5B")
    assert hf_spec is not None and hf_spec.kind == "litellm"


# ── provider (cheap, offline-safe surface) ───────────────────────────────────────────


def test_provider_reports_model_id_and_zeroed_usage_before_any_call():
    provider = LocalTransformersProvider(model="nvidia/Nemotron-Mini-4B-Instruct")
    assert provider.model_id == "nvidia/Nemotron-Mini-4B-Instruct"
    assert provider.last_usage == {}  # no call yet — matches the sibling providers
    provider._zero_usage()
    assert provider.last_usage == {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}


def test_provider_resolves_trust_remote_code_from_catalogue():
    assert LocalTransformersProvider(model="openbmb/MiniCPM4.1-8B")._trust_remote_code() is True
    assert LocalTransformersProvider(model="CohereLabs/aya-expanse-8b")._trust_remote_code() is False
    # An off-catalogue repo defaults to the safe choice.
    assert LocalTransformersProvider(model="some/random-repo")._trust_remote_code() is False


def test_provider_resolves_use_cache_from_catalogue():
    # MiniCPM disables the KV cache (its v4-era code mishandles transformers 5.x's cache);
    # native-arch models keep it on, and an off-catalogue repo defaults to the cached path.
    assert LocalTransformersProvider(model="openbmb/MiniCPM4.1-8B")._use_cache() is False
    assert LocalTransformersProvider(model="nvidia/Nemotron-Mini-4B-Instruct")._use_cache() is True
    assert LocalTransformersProvider(model="some/random-repo")._use_cache() is True


# ── ZeroGPU contract: CUDA only inside @spaces.GPU, never in the parent ───────────────
# Regression guard for the production crash "Low-level CUDA init (torch._C._cuda_init)
# reached … ZeroGPU's emulation did not intercept": the parent process gets no GPU, so any
# CUDA placement outside the @spaces.GPU window (a lazy .to("cuda") at request time) kills
# the worker. The forward pass can only be exercised with a GPU + weights (integration),
# so we pin the *structural* invariant — where CUDA may be touched — by source contract.


def test_parent_loader_never_initialises_cuda():
    import ast
    import inspect

    from src.models import local_provider

    # _ensure_loaded runs in the parent (warm CPU cache, inherited by the fork). It must
    # not perform any CUDA operation — placement happens later, inside the decorated
    # function. Check the executable body with the docstring stripped (the docstring
    # explains the invariant in prose, so it legitimately mentions CUDA); the dangerous
    # ops are the device move and any torch.cuda.* call.
    fn = ast.parse(inspect.getsource(local_provider._ensure_loaded)).body[0]
    if ast.get_docstring(fn):
        fn.body = fn.body[1:]
    code = ast.unparse(fn)
    assert 'to("cuda")' not in code
    assert "torch.cuda" not in code
    assert ".cuda(" not in code


def test_parent_loader_fully_materialises_weights_no_meta_tensors():
    import ast
    import inspect

    from src.models import local_provider

    # Regression guard for the ZeroGPU crash "Cannot copy out of meta tensor; no data!" on
    # model.to("cuda"): transformers 5.x's meta-init load leaves a tied/"missing" head (e.g.
    # Qwen2.5's lm_head, tied to embed_tokens) on the meta device. The parent loader must
    # force full CPU materialisation and re-tie the head so nothing is left on meta for the
    # in-fork device move to choke on (transformers#41038/#30703). Check the executable body
    # with the docstring stripped (the docstring explains the fix in prose, naming the same
    # kwarg), so we count the real calls, not the explanation.
    fn = ast.parse(inspect.getsource(local_provider._ensure_loaded)).body[0]
    if ast.get_docstring(fn):
        fn.body = fn.body[1:]
    code = ast.unparse(fn)
    # both from_pretrained branches (dtype= and the legacy torch_dtype= fallback) opt out of
    # the selective meta-init load…
    assert code.count("low_cpu_mem_usage=False") == 2
    # …and the head is explicitly re-tied to the materialised embeddings.
    assert "tie_weights()" in code


def test_v4_compat_shim_backfills_removed_remote_code_predicates():
    # Regression guard for the ZeroGPU error "cannot import name 'is_torch_fx_available'
    # from transformers.utils.import_utils": transformers 5.x removed these predicates, but
    # MiniCPM's (and other) trust_remote_code modelling files still import them. The provider
    # back-fills them (all True at our torch floor) so the remote import succeeds.
    from src.models import local_provider

    local_provider._ensure_transformers_v4_symbols()
    from transformers.utils import import_utils

    # Every name the shim covers is importable from transformers.utils.import_utils and True.
    for name in local_provider._REMOVED_TORCH_PREDICATES:
        fn = getattr(import_utils, name)
        assert fn() is True
    # And _ensure_loaded runs the shim before touching any remote code.
    import inspect

    assert "_ensure_transformers_v4_symbols()" in inspect.getsource(local_provider._ensure_loaded)


def test_gpu_transfer_lives_inside_the_spaces_gpu_function():
    from pathlib import Path

    from src.models import local_provider

    # _generate is wrapped by @spaces.GPU, so read the module source and isolate its block.
    module_src = Path(local_provider.__file__).read_text()
    gen_block = module_src.split("def _generate(", 1)[1].split("\ndef ", 1)[0]
    # The CPU→GPU move is here (the one place ZeroGPU grants a device)…
    assert '.to("cuda")' in gen_block
    # …and the function carries the decorator the platform registers.
    assert "@spaces.GPU" in module_src.split("def _generate(", 1)[0].rsplit("\n\n", 1)[-1]


def test_generate_unpacks_batchencoding_never_passes_a_positional_dict():
    # Regression guard for the production AttributeError "inputs_tensor.shape[0]" in
    # transformers.generate: in transformers 5.x apply_chat_template(return_tensors="pt")
    # defaults to a BatchEncoding *dict*, and passing that dict positionally into
    # model.generate(inputs) makes generate() do .shape on a dict. The fix: request the
    # dict explicitly (return_dict=True) and unpack it with ** so input_ids + attention_mask
    # are fed as kwargs. Pinned by AST so the call shape can't silently regress.
    import ast
    from pathlib import Path

    from src.models import local_provider

    tree = ast.parse(Path(local_provider.__file__).read_text())
    gen = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "_generate")
    calls = [c for c in ast.walk(gen) if isinstance(c, ast.Call)]

    # apply_chat_template asks for the dict form explicitly (robust whatever the default).
    act = next(c for c in calls if isinstance(c.func, ast.Attribute) and c.func.attr == "apply_chat_template")
    assert any(k.arg == "return_dict" and k.value.value is True for k in act.keywords)

    # model.generate(**inputs, …): the encoding is unpacked, never a positional dict.
    gen_call = next(c for c in calls if isinstance(c.func, ast.Attribute) and c.func.attr == "generate")
    assert not gen_call.args, "generate() must take no positional arg (the old bug passed the dict positionally)"
    assert any(k.arg is None and isinstance(k.value, ast.Name) and k.value.id == "inputs" for k in gen_call.keywords)
    # use_cache is threaded through so a model with broken 5.x cache handling (MiniCPM) can
    # disable it ("Key and Value must have the same sequence length").
    assert any(k.arg == "use_cache" for k in gen_call.keywords)
