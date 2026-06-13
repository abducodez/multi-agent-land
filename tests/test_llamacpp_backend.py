"""Tests for the llama.cpp local backend — catalogue, registry integration, launcher.

llama.cpp is the third inference backend (next to Modal and Hugging Face): GGUF models
served locally by ``llama-server`` behind an OpenAI-compatible API. These tests cover the
read/binding side (catalogue + unified registry) and the serving side (the launcher's
pure command-building and GPU detection) without a GPU or the binary present — the
launcher's pure functions take platform/probe as arguments precisely so they're testable.
"""

from __future__ import annotations

import pytest

from src.models import inference, llamacpp_catalogue, llamacpp_server


# ── catalogue ────────────────────────────────────────────────────────────────────────


def test_catalogue_covers_three_sponsor_tiers():
    by_profile = {m.profile: m for m in llamacpp_catalogue.LLAMACPP_MODELS}
    # Nemotron (tiny ≤4B), MiniCPM (fast ≤8B), Mellum (balanced ≤13B) — the three lanes.
    assert by_profile["tiny"].params_b <= 4
    assert by_profile["fast"].params_b <= 8
    assert by_profile["balanced"].params_b <= 13
    # Every model stays within the ≤32B "small minds" rule.
    assert all(m.params_b <= 32 for m in llamacpp_catalogue.LLAMACPP_MODELS)


def test_hf_spec_appends_quant_only_when_not_baked_into_repo():
    nemotron = llamacpp_catalogue.model_by_key("nemotron-3-nano-4b")
    mellum = llamacpp_catalogue.model_by_key("mellum2-12b-thinking")
    # Multi-quant repo → the quant is selected with the ``:QUANT`` form.
    assert nemotron.hf_spec == "nvidia/NVIDIA-Nemotron-3-Nano-4B-GGUF:Q4_K_M"
    # Quant already baked into the repo name → bare repo, no ``:QUANT`` suffix.
    assert mellum.quant is None
    assert mellum.hf_spec == "JetBrains/Mellum2-12B-A2.5B-Thinking-GGUF-Q4_K_M"


def test_served_id_is_the_stable_key_not_the_gguf_name():
    # The launcher serves under --alias <key>, so the engine binds to a stable id even as
    # GGUF repo/quant names churn.
    m = llamacpp_catalogue.model_by_key("minicpm-4-1-8b")
    assert m.served_model_id == "minicpm-4-1-8b"


def test_binding_uses_local_url_and_placeholder_key_by_default():
    binding = llamacpp_catalogue.binding_for("nemotron-3-nano-4b", env={})
    assert binding["model"] == "openai/nemotron-3-nano-4b"
    assert binding["base_url"] == llamacpp_catalogue.DEFAULT_BASE_URL
    # llama-server ignores the token but OpenAI clients require a non-empty one.
    assert binding["api_key"]


def test_binding_honours_env_overrides():
    env = {"LLAMACPP_BASE_URL": "http://gpu-box:9000/v1", "LLAMACPP_API_KEY": "sekret"}
    binding = llamacpp_catalogue.binding_for("minicpm-4-1-8b", env=env)
    assert binding["base_url"] == "http://gpu-box:9000/v1"
    assert binding["api_key"] == "sekret"


def test_binding_unknown_key_raises():
    with pytest.raises(KeyError):
        llamacpp_catalogue.binding_for("does-not-exist", env={})


def test_has_credentials_gates_on_explicit_base_url():
    # No silent "live": the backend is opted in only when the URL is set (the launcher
    # sets it, or you export it to point at a running/remote server).
    assert llamacpp_catalogue.has_credentials(env={}) is False
    assert llamacpp_catalogue.has_credentials(env={"LLAMACPP_BASE_URL": "http://x/v1"}) is True


# ── unified registry integration ──────────────────────────────────────────────────────


def test_registered_as_third_backend():
    assert "llamacpp" in {b.key for b in inference.backends()}
    keys = {e["key"] for e in inference.entries("llamacpp")}
    assert all(k.startswith("llamacpp:") for k in keys)


def test_registry_dispatches_binding_and_availability():
    key = inference.default_key_for_profile("tiny", "llamacpp")
    assert key == "llamacpp:nemotron-3-nano-4b"
    binding = inference.binding_for(key, env={"LLAMACPP_BASE_URL": "http://127.0.0.1:8080/v1"})
    assert binding["base_url"] == "http://127.0.0.1:8080/v1"
    assert inference.backend_available("llamacpp", env={"LLAMACPP_BASE_URL": "http://x/v1"}) is True
    assert inference.backend_available("llamacpp", env={}) is False


def test_configured_backends_includes_llamacpp_when_url_set():
    configured = inference.configured_backends(env={"LLAMACPP_BASE_URL": "http://x/v1"})
    assert "llamacpp" in configured
    assert inference.configured_backends(env={}) == []


# ── launcher: GPU detection ────────────────────────────────────────────────────────────


def test_detect_accelerator_metal_on_macos():
    assert llamacpp_server.detect_accelerator(platform="darwin") == "metal"


def test_detect_accelerator_cuda_when_gpu_present():
    assert llamacpp_server.detect_accelerator(platform="linux", probe=lambda: True) == "cuda"


def test_detect_accelerator_cpu_when_no_gpu():
    assert llamacpp_server.detect_accelerator(platform="linux", probe=lambda: False) == "cpu"


def test_gpu_layers_offloads_all_on_gpu_none_on_cpu():
    assert llamacpp_server.gpu_layers("metal") == 999
    assert llamacpp_server.gpu_layers("cuda") == 999
    assert llamacpp_server.gpu_layers("cpu") == 0


# ── launcher: command building ─────────────────────────────────────────────────────────


def test_build_command_offloads_layers_on_gpu():
    model = llamacpp_catalogue.model_by_key("nemotron-3-nano-4b")
    cmd = llamacpp_server.build_command(model, accelerator="cuda")
    assert cmd[0] == "llama-server"
    assert "-hf" in cmd and model.hf_spec in cmd
    assert cmd[cmd.index("--alias") + 1] == "nemotron-3-nano-4b"
    assert "-ngl" in cmd and cmd[cmd.index("-ngl") + 1] == "999"


def test_build_command_omits_offload_on_cpu():
    model = llamacpp_catalogue.model_by_key("nemotron-3-nano-4b")
    cmd = llamacpp_server.build_command(model, accelerator="cpu")
    assert "-ngl" not in cmd


def test_build_command_carries_model_sampling_and_ctx():
    model = llamacpp_catalogue.model_by_key("mellum2-12b-thinking")
    cmd = llamacpp_server.build_command(model, accelerator="metal")
    assert cmd[cmd.index("--temp") + 1] == "0.6"
    assert cmd[cmd.index("--top-k") + 1] == "20"
    assert cmd[cmd.index("--ctx-size") + 1] == "16384"
    assert "--flash-attn" in cmd


def test_build_command_ctx_override_wins():
    model = llamacpp_catalogue.model_by_key("mellum2-12b-thinking")
    cmd = llamacpp_server.build_command(model, accelerator="cpu", ctx_size=2048)
    assert cmd[cmd.index("--ctx-size") + 1] == "2048"


def test_base_url_for_advertises_loopback_when_bound_to_all_interfaces():
    assert llamacpp_server.base_url_for("0.0.0.0", 8080) == "http://127.0.0.1:8080/v1"
    assert llamacpp_server.base_url_for("127.0.0.1", 9000) == "http://127.0.0.1:9000/v1"
