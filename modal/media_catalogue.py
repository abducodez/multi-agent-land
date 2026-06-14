"""Media model catalogue — image + TTS models served behind OpenAI-compatible routes.

Kept separate from the text-model ``catalogue.py`` (and stdlib-only, no Modal import) so
the chat-model lists the Lab and router read stay clean. The image/TTS Modal apps
(``app_images.py`` / ``app_tts.py``) consume these configs to stand up endpoints; the
engine resolves the *same* endpoints from ``config/media.yaml`` via the shared Modal URL
convention (``…--<app>-<endpoint>.modal.run/v1``), so the two never drift.

Both models stay small (a 4B FLUX.2 [klein] image model, a 2B VoxCPM2 TTS model) — the
≤4B "Tiny Titan" story. FLUX.2 [klein] 4B is the *distilled*, few-step variant, so a beat
lands quickly (~8 steps, no CFG); keep one container warm for the live demo. VoxCPM2 rides
a MiniCPM-4 backbone — an OpenBMB model — so the cast doubles as the OpenBMB track entry.
"""

from __future__ import annotations

from dataclasses import dataclass

# App names — must match config/media.yaml `app:` and the modal.App(...) in app_*.py.
IMAGE_APP = "image-gen"
TTS_APP = "audio-tts"

# Modal Secret holding a Hugging Face token (key: HF_TOKEN), mounted for gated repos —
# the same secret the text service uses. Create it once with:
#   modal secret create huggingface-secret HF_TOKEN=hf_...
HF_SECRET_NAME = "huggingface-secret"

# Shared base image bits.
CUDA_IMAGE = "nvidia/cuda:12.9.0-devel-ubuntu22.04"
PYTHON_VERSION = "3.13"  # must match the deploy venv (serialized functions, see service.py)
HF_CACHE_PATH = "/root/.cache/huggingface"


@dataclass(frozen=True)
class ImageModel:
    repo_id: str
    endpoint_name: str
    # The diffusers pipeline class to load. ``AutoPipelineForText2Image`` autodetects for
    # most models, but newer architectures (FLUX.2) need their explicit class, resolved by
    # name from the ``diffusers`` module at serve time.
    pipeline_class: str = "AutoPipelineForText2Image"
    dtype: str = "float16"  # FLUX.2 wants "bfloat16"; turbo SD ran fp16
    steps: int = 2  # turbo/distilled diffusion needs very few denoising steps
    guidance: float | None = 0.0  # None → omit guidance_scale entirely (distilled FLUX is CFG-free)
    # ``enable_model_cpu_offload()`` instead of ``.to("cuda")`` — keeps peak VRAM low at
    # a little latency. Off by default: a 4B model fits an L40S on-device, which is fastest.
    cpu_offload: bool = False
    gated: bool = False  # mount the HF token secret (HF_SECRET_NAME) for gated repos
    gpu: str = "L40S:1"
    max_concurrent_inputs: int = 6
    scaledown_window: int = 10 * 60
    startup_timeout: int = 20 * 60
    request_timeout: int = 10 * 60
    extra_pip: tuple[str, ...] = (
        "diffusers>=0.30",
        "transformers>=4.44",
        "accelerate>=0.33",
        "pillow",
        "fastapi",
        "uvicorn[standard]",
    )


@dataclass(frozen=True)
class TTSModel:
    repo_id: str
    endpoint_name: str
    # VoxCPM2's AudioVAE V2 outputs 48kHz studio-quality audio (no external upsampler). The
    # serve loop reads the true rate off the loaded model; this is the documented default.
    sample_rate: int = 48000
    # "default" → context-aware synthesis (no voice tag); any other string is treated as a
    # natural-language *voice design* description (gender, age, tone, pace…) and prepended to
    # the line in parentheses, so the OpenAI ``voice`` field drives VoxCPM2's voice design.
    default_voice: str = "default"
    # VoxCPM2 is multilingual with no language tag, so no lang_code is needed. cfg_value and
    # inference_timesteps are the generation knobs from the model card (2.0 / 10).
    cfg_value: float = 2.0
    inference_timesteps: int = 10
    gpu: str = "L4:1"  # 2B model, ~8GB VRAM — comfortable on an L4
    max_concurrent_inputs: int = 8
    scaledown_window: int = 10 * 60
    startup_timeout: int = 15 * 60
    request_timeout: int = 5 * 60
    extra_pip: tuple[str, ...] = ("voxcpm", "soundfile", "fastapi", "uvicorn[standard]")


# FLUX.2 [klein] 4B — the *distilled* variant: few-step (great for demo latency) and
# guidance-free. ``DiffusionPipeline.from_pretrained`` auto-resolves the Flux2 pipeline
# class from the repo, so no explicit class is needed. FLUX.2 is supported in released
# diffusers (no git install). Mirrors a verified working Modal recipe.
IMAGE_MODELS: tuple[ImageModel, ...] = (
    ImageModel(
        repo_id="black-forest-labs/FLUX.2-klein-4B",
        endpoint_name="flux2-klein",
        pipeline_class="DiffusionPipeline",  # auto-resolves the Flux2 pipeline from the repo
        dtype="bfloat16",
        steps=8,  # distilled → very few steps for a fast beat
        guidance=None,  # distilled → no CFG (don't pass guidance_scale)
        cpu_offload=False,  # 4B distilled fits an L40S on-device; fastest path
        gated=True,  # FLUX.2 repo needs an HF token to download
        extra_pip=(
            "diffusers",
            "transformers",
            "accelerate",
            "sentencepiece",
            "peft",
            "pillow",
            "fastapi[standard]",
        ),
    ),
)

# VoxCPM2 — a 2B tokenizer-free diffusion-autoregressive TTS on a MiniCPM-4 backbone
# (OpenBMB). 30 languages with no language tag, 48kHz output, voice design from a text
# description alone. ``VoxCPM.from_pretrained`` pulls the weights from the HF cache volume.
TTS_MODELS: tuple[TTSModel, ...] = (TTSModel(repo_id="openbmb/VoxCPM2", endpoint_name="voxcpm2"),)
