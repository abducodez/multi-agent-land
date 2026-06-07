"""Declarative catalogue of every servable model, grouped by provider.

This is the single place to add, remove, or retune a model. Each provider app
imports only its own list, so providers stay isolated in separate Modal apps.

GPU sizing notes (starting points — tune against real memory use):
- BF16 weights ≈ 2 bytes/param. Leave headroom for the KV cache.
- MoE models (A3B / A4B) load all expert weights but only activate a slice,
  so size GPU memory to the *total* parameter count, not the active count.
- Cap ``max_model_len`` to trade context length for KV-cache memory / throughput.

Parser names (``reasoning_parser`` / ``tool_call_parser``) are vLLM-version
specific. They are left conservative here; enable per model once verified
against the deployed vLLM version, otherwise vLLM rejects an unknown parser.
"""

from __future__ import annotations

from service import ModelConfig

# --- NVIDIA --------------------------------------------------------------------

NVIDIA_MODELS: list[ModelConfig] = [
    ModelConfig(
        name="nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B-BF16",
        endpoint_name="nemotron-3-nano-30b",
        # 30B total params in BF16 (~60GB) though only ~3B activate per token.
        gpu="H200:1",
        max_model_len=32768,
        trust_remote_code=True,
        gated=True,
        max_concurrent_inputs=64,
    ),
    ModelConfig(
        name="nvidia/NVIDIA-Nemotron-3-Nano-4B-BF16",
        endpoint_name="nemotron-3-nano-4b",
        # Tiny Titan tier (≤4B): comfortably fits a single 24GB L4.
        gpu="L4:1",
        max_model_len=16384,
        trust_remote_code=True,
        gated=True,
        max_concurrent_inputs=32,
    ),
]

# --- OpenBMB (MiniCPM) ---------------------------------------------------------

OPENBMB_MODELS: list[ModelConfig] = [
    ModelConfig(
        name="openbmb/MiniCPM-o-4_5",
        endpoint_name="minicpm-o-4-5",
        # Omni-modal (text + vision + audio). Needs custom code and media backends.
        gpu="L40S:1",
        trust_remote_code=True,
        multimodal=True,
        mm_limits={"image": 4, "audio": 2, "video": 1},
        # Audio/vision preprocessing backends pulled into the image.
        extra_pip=("librosa", "soundfile", "timm"),
        max_concurrent_inputs=16,
    ),
    ModelConfig(
        name="openbmb/MiniCPM4.1-8B",
        endpoint_name="minicpm-4-1-8b",
        gpu="L40S:1",
        max_model_len=32768,
        trust_remote_code=True,
        max_concurrent_inputs=48,
    ),
]

# --- Google (Gemma) ------------------------------------------------------------

GOOGLE_MODELS: list[ModelConfig] = [
    ModelConfig(
        name="google/gemma-4-26B-A4B-it",
        endpoint_name="gemma-4-26b",
        # MoE: ~26B total params (~4B active). Gated repo — needs an HF token.
        gpu="H200:1",
        max_model_len=32768,
        gated=True,
        reasoning_parser="gemma4",
        tool_call_parser="gemma4",
        enable_auto_tool_choice=True,
        max_concurrent_inputs=64,
    ),
    ModelConfig(
        name="google/gemma-4-12B",
        endpoint_name="gemma-4-12b",
        gpu="L40S:1",
        max_model_len=32768,
        gated=True,
        reasoning_parser="gemma4",
        tool_call_parser="gemma4",
        enable_auto_tool_choice=True,
        max_concurrent_inputs=48,
    ),
]

# Convenience: every model across providers (handy for tooling / docs).
ALL_MODELS: list[ModelConfig] = NVIDIA_MODELS + OPENBMB_MODELS + GOOGLE_MODELS
