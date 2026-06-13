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


def test_only_tiny_is_a_tier_default_and_sizes_stay_small():
    # Exactly one tier default (tiny) so the whole cast routes to it unless a seat is
    # pinned — the latency/ZeroGPU-quota guardrail. Every model honours the ≤32B rule.
    tagged = [m for m in local_catalogue.LOCAL_MODELS if m.profile is not None]
    assert [m.profile for m in tagged] == ["tiny"]
    assert all(m.params_b is None or m.params_b <= 32 for m in local_catalogue.LOCAL_MODELS)
    tiny = local_catalogue.model_by_key(local_catalogue.default_key_for_profile("tiny"))
    assert tiny is not None and tiny.params_b <= 4  # Tiny-Titan band


def test_only_tiny_has_a_default_other_tiers_fall_through():
    assert local_catalogue.default_key_for_profile("tiny") is not None
    for tier in ("fast", "balanced", "strong"):
        assert local_catalogue.default_key_for_profile(tier) is None


def test_model_by_key_carries_trust_remote_code():
    # MiniCPM ships custom modelling code; Qwen does not; an off-catalogue id is unknown.
    assert local_catalogue.model_by_key("openbmb/MiniCPM4.1-8B").trust_remote_code is True
    assert local_catalogue.model_by_key("Qwen/Qwen2.5-3B-Instruct").trust_remote_code is False
    assert local_catalogue.model_by_key("does/not-exist") is None


def test_binding_is_a_bare_repo_id_with_no_endpoint():
    # In-process: the binding carries the raw transformers repo id (no openai/ prefix) and
    # neither a base_url nor an api_key — the router builds the in-process provider from it.
    binding = local_catalogue.binding_for("Qwen/Qwen2.5-3B-Instruct")
    assert binding["model"] == "Qwen/Qwen2.5-3B-Instruct"
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
    assert key == "local:Qwen/Qwen2.5-3B-Instruct"
    binding = inference.binding_for(key)
    assert binding["model"] == "Qwen/Qwen2.5-3B-Instruct"
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
    provider = router.for_profile("local:Qwen/Qwen2.5-3B-Instruct")
    assert isinstance(provider, LocalTransformersProvider)
    assert provider.model == "Qwen/Qwen2.5-3B-Instruct"
    assert provider.model_id == "Qwen/Qwen2.5-3B-Instruct"


def test_catalogue_spec_tags_local_kind_and_others_litellm():
    router = ModelRouter(offline=False)
    local_spec = router._catalogue_spec("local:Qwen/Qwen2.5-3B-Instruct")
    assert local_spec is not None and local_spec.kind == "local"
    # An HF key resolves through the same path but stays on the HTTP transport.
    hf_spec = router._catalogue_spec("hf:katanemo/Arch-Router-1.5B")
    assert hf_spec is not None and hf_spec.kind == "litellm"


# ── provider (cheap, offline-safe surface) ───────────────────────────────────────────


def test_provider_reports_model_id_and_zeroed_usage_before_any_call():
    provider = LocalTransformersProvider(model="Qwen/Qwen2.5-3B-Instruct")
    assert provider.model_id == "Qwen/Qwen2.5-3B-Instruct"
    assert provider.last_usage == {}  # no call yet — matches the sibling providers
    provider._zero_usage()
    assert provider.last_usage == {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}


def test_provider_resolves_trust_remote_code_from_catalogue():
    assert LocalTransformersProvider(model="openbmb/MiniCPM4.1-8B")._trust_remote_code() is True
    assert LocalTransformersProvider(model="Qwen/Qwen2.5-3B-Instruct")._trust_remote_code() is False
    # An off-catalogue repo defaults to the safe choice.
    assert LocalTransformersProvider(model="some/random-repo")._trust_remote_code() is False


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
