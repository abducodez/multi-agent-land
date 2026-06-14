"""Media model catalogue — image + TTS models served behind OpenAI-compatible routes.

Kept separate from the text-model ``catalogue.py`` (and stdlib-only, no Modal import) so
the chat-model lists the Lab and router read stay clean. The image/TTS Modal apps
(``app_images.py`` / ``app_tts.py``) consume these configs to stand up endpoints; the
engine resolves the *same* endpoints from ``config/media.yaml`` via the shared Modal URL
convention (``…--<app>-<endpoint>.modal.run/v1``), so the two never drift.

Both models are intentionally small (a ~1B single-step diffusion image model, an ~82M TTS
model) — the ≤4B "Tiny Titan" story, and fast enough that the commentator's beat lands
without stalling the show (keep one container warm for the live demo).
"""

from __future__ import annotations

from dataclasses import dataclass

# App names — must match config/media.yaml `app:` and the modal.App(...) in app_*.py.
IMAGE_APP = "image-gen"
TTS_APP = "audio-tts"

# Shared base image bits.
CUDA_IMAGE = "nvidia/cuda:12.9.0-devel-ubuntu22.04"
PYTHON_VERSION = "3.13"  # must match the deploy venv (serialized functions, see service.py)
HF_CACHE_PATH = "/root/.cache/huggingface"


@dataclass(frozen=True)
class ImageModel:
    repo_id: str
    endpoint_name: str
    steps: int = 2  # turbo diffusion needs very few denoising steps
    guidance: float = 0.0  # turbo models run guidance-free
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
    sample_rate: int = 24000
    lang_code: str = "a"  # Kokoro: 'a' = American English
    default_voice: str = "af_heart"
    gpu: str = "L4:1"
    max_concurrent_inputs: int = 8
    scaledown_window: int = 10 * 60
    startup_timeout: int = 15 * 60
    request_timeout: int = 5 * 60
    extra_pip: tuple[str, ...] = ("kokoro>=0.9", "soundfile", "fastapi", "uvicorn[standard]")


IMAGE_MODELS: tuple[ImageModel, ...] = (ImageModel(repo_id="stabilityai/sd-turbo", endpoint_name="sd-turbo", steps=2),)

TTS_MODELS: tuple[TTSModel, ...] = (TTSModel(repo_id="hexgrad/Kokoro-82M", endpoint_name="kokoro-82m"),)
