"""Guard the precision flags ``build_command`` emits into the vLLM argv.

Quantization is purely serving-side: it only adds ``--quantization`` /
``--kv-cache-dtype`` to the ``vllm serve`` argv (the ``--served-model-name`` is
unchanged, so the engine never notices). Two controls feed those flags — a
per-model ``ModelConfig`` field and a deploy-time env override that wins over it
— and these tests pin both, plus the force-disable token, since this is the first
test to assert on ``build_command``'s output at all.

``modal/service.py`` does ``import modal`` and ``from catalogue import …``, so we
load it exactly the way ``modal deploy`` does: with ``modal/`` on ``sys.path`` (the
folder's contents become importable under their bare names; ``import modal`` still
binds the installed SDK, not the folder).
"""

from __future__ import annotations

import importlib
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


# ── per-model field ──────────────────────────────────────────────────────────


def test_no_quantization_by_default(service):
    cmd = service.build_command(_make(service))
    assert "--quantization" not in cmd
    assert "--kv-cache-dtype" not in cmd


def test_per_model_quantization_emits_flag(service):
    cmd = service.build_command(_make(service, quantization="fp8"))
    assert cmd[cmd.index("--quantization") + 1] == "fp8"


def test_per_model_kv_cache_dtype_emits_flag(service):
    cmd = service.build_command(_make(service, kv_cache_dtype="fp8"))
    assert cmd[cmd.index("--kv-cache-dtype") + 1] == "fp8"


# ── deploy-time env override ───────────────────────────────────────────────────


def test_env_override_beats_unset_model_field(service, monkeypatch):
    monkeypatch.setattr(service, "QUANTIZATION", "fp8")
    cmd = service.build_command(_make(service))  # model field is None
    assert cmd[cmd.index("--quantization") + 1] == "fp8"


def test_env_override_beats_model_field(service, monkeypatch):
    monkeypatch.setattr(service, "QUANTIZATION", "awq")
    cmd = service.build_command(_make(service, quantization="fp8"))
    assert cmd[cmd.index("--quantization") + 1] == "awq"


@pytest.mark.parametrize("token", ["none", "off", "bf16", "AUTO"])
def test_disable_token_forces_full_precision(service, monkeypatch, token):
    # A model that defaults to fp8 is overridden back to no flag at deploy time.
    monkeypatch.setattr(service, "QUANTIZATION", token)
    cmd = service.build_command(_make(service, quantization="fp8"))
    assert "--quantization" not in cmd


def test_kv_cache_env_override(service, monkeypatch):
    monkeypatch.setattr(service, "KV_CACHE_DTYPE", "fp8")
    cmd = service.build_command(_make(service))
    assert cmd[cmd.index("--kv-cache-dtype") + 1] == "fp8"


# ── FP8 KV cache × snapshot incompatibility (vLLM wake-path crash) ─────────────


def test_fp8_kv_cache_dropped_for_snapshot_models(service):
    # FP8 KV cache crashes the /wake_up path on snapshot models, so the flag is
    # suppressed when gpu_snapshot is set — the endpoint serves with full-precision
    # KV cache rather than booting into a state it can never wake from.
    cmd = service.build_command(_make(service, kv_cache_dtype="fp8", gpu_snapshot=True))
    assert "--kv-cache-dtype" not in cmd
    # The snapshot flag itself still wins and is emitted.
    assert "--enable-sleep-mode" in cmd


def test_fp8_kv_cache_env_override_dropped_for_snapshot_models(service, monkeypatch):
    # The global deploy override is the common trigger: it lands on every model in
    # the app, including snapshot ones, which must still drop it.
    monkeypatch.setattr(service, "KV_CACHE_DTYPE", "fp8")
    cmd = service.build_command(_make(service, gpu_snapshot=True))
    assert "--kv-cache-dtype" not in cmd


def test_fp8_variant_kv_cache_dropped_for_snapshot_models(service):
    # Every fp8 variant hits init_fp8_kv_scales, so fp8_e5m2 is dropped too.
    cmd = service.build_command(_make(service, kv_cache_dtype="fp8_e5m2", gpu_snapshot=True))
    assert "--kv-cache-dtype" not in cmd


def test_non_fp8_kv_cache_kept_for_snapshot_models(service):
    # The guard only fires on fp8; a non-fp8 dtype passes through even with snapshot.
    cmd = service.build_command(_make(service, kv_cache_dtype="auto", gpu_snapshot=True))
    assert cmd[cmd.index("--kv-cache-dtype") + 1] == "auto"


def test_fp8_kv_cache_kept_for_non_snapshot_models(service):
    # Without snapshot there's no wake path, so FP8 KV cache stays.
    cmd = service.build_command(_make(service, kv_cache_dtype="fp8", gpu_snapshot=False))
    assert cmd[cmd.index("--kv-cache-dtype") + 1] == "fp8"


# ── deploy script wiring ───────────────────────────────────────────────────────


def test_deploy_script_propagates_quantization_env():
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    deploy_modal = importlib.import_module("deploy_modal")
    from argparse import Namespace

    base = dict(keep_warm=False, auth=False, json_logs=False, log_level="", kv_cache_dtype=None)
    env_fp8 = deploy_modal._env_for(Namespace(quantization="fp8", **base))
    assert env_fp8["MODAL_LLM_QUANTIZATION"] == "fp8"

    # ``--quantization none`` (force full precision) is still propagated, not dropped.
    env_none = deploy_modal._env_for(Namespace(quantization="none", **base))
    assert env_none["MODAL_LLM_QUANTIZATION"] == "none"

    # Unset → the env var is left alone (so a model's own default stands).
    env_unset = deploy_modal._env_for(Namespace(quantization=None, **base))
    assert "MODAL_LLM_QUANTIZATION" not in env_unset
