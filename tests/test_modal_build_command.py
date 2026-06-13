"""Guard the ``vllm serve`` argv that ``build_command`` emits.

The serving layer turns one ``ModelConfig`` into the argv launched inside the
container, so these tests pin the mapping from config fields to vLLM flags: the
always-present identity flags, the data-driven toggles (parsers, eager, prefix
caching), and the ``extra_vllm_args`` escape hatch.

``modal/service.py`` does ``import modal`` and ``from catalogue import …``, so we
load it exactly the way ``modal deploy`` does: with ``modal/`` on ``sys.path`` (the
folder's contents become importable under their bare names; ``import modal`` still
binds the installed SDK, not the folder).
"""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

import pytest

_MODAL_DIR = Path(__file__).resolve().parents[1] / "modal"


@pytest.fixture(scope="module")
def service():
    """The serving module, importable with ``modal/`` on the path (as at deploy time)."""
    if str(_MODAL_DIR) not in sys.path:
        sys.path.insert(0, str(_MODAL_DIR))
    return importlib.import_module("service")


def _make(service, **kwargs):
    """A minimal valid ModelConfig with overridable fields."""
    return service.ModelConfig(name="acme/Tiny-1B", endpoint_name="tiny-1b", **kwargs)


def _flag_value(cmd: list[str], flag: str) -> str:
    """The argument that follows ``flag`` in the argv."""
    return cmd[cmd.index(flag) + 1]


# ── always-present identity flags ──────────────────────────────────────────────


def test_serves_the_model_with_identity_flags(service):
    cmd = service.build_command(_make(service))
    assert cmd[:3] == ["vllm", "serve", "acme/Tiny-1B"]
    # served-model-name defaults to the repo name (clients pass the repo id).
    assert _flag_value(cmd, "--served-model-name") == "acme/Tiny-1B"
    assert _flag_value(cmd, "--port") == str(service.VLLM_PORT)
    assert _flag_value(cmd, "--tensor-parallel-size") == "1"


def test_served_model_name_alias(service):
    cmd = service.build_command(_make(service, served_model_name="acme/Tiny"))
    assert _flag_value(cmd, "--served-model-name") == "acme/Tiny"
    # but vLLM still loads the real repo (positional arg)
    assert cmd[2] == "acme/Tiny-1B"


# ── data-driven toggles ────────────────────────────────────────────────────────


def test_prefix_caching_on_by_default_off_when_disabled(service):
    assert "--enable-prefix-caching" in service.build_command(_make(service))
    off = service.build_command(_make(service, enable_prefix_caching=False))
    assert "--no-enable-prefix-caching" in off
    assert "--enable-prefix-caching" not in off


def test_optional_inference_flags_emitted(service):
    cmd = service.build_command(
        _make(
            service,
            max_model_len=8192,
            trust_remote_code=True,
            enforce_eager=True,
            gpu_memory_utilization=0.9,
        )
    )
    assert _flag_value(cmd, "--max-model-len") == "8192"
    assert "--trust-remote-code" in cmd
    assert "--enforce-eager" in cmd
    assert _flag_value(cmd, "--gpu-memory-utilization") == "0.9"


def test_async_scheduling_default_on_off_when_disabled(service):
    assert "--async-scheduling" in service.build_command(_make(service))
    assert "--async-scheduling" not in service.build_command(_make(service, async_scheduling=False))


def test_parser_flags(service):
    cmd = service.build_command(
        _make(service, reasoning_parser="qwen3", tool_call_parser="hermes", enable_auto_tool_choice=True)
    )
    assert _flag_value(cmd, "--reasoning-parser") == "qwen3"
    assert _flag_value(cmd, "--tool-call-parser") == "hermes"
    assert "--enable-auto-tool-choice" in cmd
    # None parsers emit nothing.
    bare = service.build_command(_make(service))
    assert "--reasoning-parser" not in bare
    assert "--tool-call-parser" not in bare


def test_mm_limits_serialized_as_json(service):
    cmd = service.build_command(_make(service, mm_limits={"image": 0, "audio": 0}))
    assert json.loads(_flag_value(cmd, "--limit-mm-per-prompt")) == {"image": 0, "audio": 0}


def test_log_requests_default_on(service):
    assert "--enable-log-requests" in service.build_command(_make(service))
    assert "--enable-log-requests" not in service.build_command(_make(service, log_requests=False))


# ── escape hatch ────────────────────────────────────────────────────────────────


def test_extra_vllm_args_appended_verbatim(service):
    cmd = service.build_command(_make(service, extra_vllm_args=("--quantization", "fp8")))
    assert cmd[-2:] == ["--quantization", "fp8"]


# ── deploy script wiring ───────────────────────────────────────────────────────


def test_deploy_script_propagates_knob_envs():
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    deploy_modal = importlib.import_module("deploy_modal")
    from argparse import Namespace

    env = deploy_modal._env_for(Namespace(keep_warm=True, auth=True))
    assert env["MODAL_LLM_KEEP_WARM"] == "1"
    assert env["MODAL_LLM_REQUIRE_AUTH"] == "1"

    # Both off → neither env var is set (so endpoints stay public + scale-to-zero).
    env_off = deploy_modal._env_for(Namespace(keep_warm=False, auth=False))
    assert "MODAL_LLM_KEEP_WARM" not in env_off
    assert "MODAL_LLM_REQUIRE_AUTH" not in env_off
