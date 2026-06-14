"""Text-to-speech serving for Modal — an OpenAI-compatible ``/v1/audio/speech`` route.

Same shape as ``image_service.py``: one autoscaling ``@app.function`` per model serving a
small FastAPI ASGI app that loads the TTS model once per container and answers the OpenAI
speech shape (``{model, input, voice, response_format}`` → WAV bytes). The engine's OpenAI
SDK client (``client.audio.speech.create``) calls it unchanged. The model is VoxCPM2 — a 2B
tokenizer-free diffusion-autoregressive TTS on a MiniCPM-4 backbone — under the ≤4B "Tiny
Titan" bar and ~8GB VRAM, so it runs comfortably on a small GPU.

Deploy:  uv run scripts/deploy_modal.py tts --keep-warm
"""

from __future__ import annotations

from collections.abc import Iterable

import modal

from media_catalogue import CUDA_IMAGE, HF_CACHE_PATH, PYTHON_VERSION, TTSModel

hf_cache_vol = modal.Volume.from_name("huggingface-cache", create_if_missing=True)


def build_image(cfg: TTSModel) -> modal.Image:
    # VoxCPM2 is tokenizer-free, but its dep chain (voxcpm → funasr → editdistance) compiles
    # a Cython C++ extension from source at install time. Modal's add_python uses a
    # python-build-standalone interpreter whose sysconfig compiler is clang/clang++, so the
    # build needs clang (+ a C/C++ toolchain) on PATH — the CUDA image ships neither by
    # default (the "clang++: No such file or directory" build failure). ffmpeg backs
    # torchaudio's audio I/O. VoxCPM2 wants torch ≥2.5; the CUDA 12.9 base meets its CUDA
    # ≥12.0 floor.
    return (
        modal.Image.from_registry(CUDA_IMAGE, add_python=PYTHON_VERSION)
        .entrypoint([])
        .apt_install("ffmpeg", "clang", "build-essential")
        .uv_pip_install("torch>=2.5.0", *cfg.extra_pip)
        .env({"HF_HUB_CACHE": HF_CACHE_PATH, "HF_XET_HIGH_PERFORMANCE": "1"})
    )


def register_tts_model(app: modal.App, cfg: TTSModel) -> modal.Function:
    image = build_image(cfg)
    target_inputs = max(1, (cfg.max_concurrent_inputs * 3) // 4)

    # Capture plain primitives (no catalogue class) into the closure: with serialized=True
    # the function is unpickled in the container, which lacks the ``media_catalogue`` module —
    # referencing ``cfg`` directly inside serve() crashes the container on deserialize
    # (ModuleNotFoundError). Mirrors service.py's serialized serve(), which captures only
    # primitives.
    repo_id = cfg.repo_id
    default_voice = cfg.default_voice
    default_sample_rate = cfg.sample_rate
    cfg_value = cfg.cfg_value
    inference_timesteps = cfg.inference_timesteps

    @app.function(
        name=cfg.endpoint_name,
        image=image,
        gpu=cfg.gpu,
        volumes={HF_CACHE_PATH: hf_cache_vol},
        scaledown_window=cfg.scaledown_window,
        timeout=cfg.request_timeout,
        serialized=True,
    )
    @modal.concurrent(max_inputs=cfg.max_concurrent_inputs, target_inputs=target_inputs)
    @modal.asgi_app()
    def serve():
        import io

        import numpy as np
        import soundfile as sf
        from fastapi import FastAPI, Response
        from voxcpm import VoxCPM

        # Load once per container; load_denoiser=False keeps startup lean (no reference-audio
        # denoiser — we synthesize from text/voice-design, not from a noisy reference clip).
        model = VoxCPM.from_pretrained(repo_id, load_denoiser=False)
        sample_rate = int(getattr(model.tts_model, "sample_rate", default_sample_rate))

        web = FastAPI()

        @web.get("/v1/models")
        def models() -> dict:
            return {"object": "list", "data": [{"id": repo_id, "object": "model"}]}

        # Body as a ``dict`` param, not a raw ``Request``: with stringized annotations
        # (``from __future__ import annotations``) FastAPI can't resolve the locally-imported
        # ``Request`` type and mis-reads it as a query field (422). ``dict`` resolves via
        # builtins and is parsed as the JSON body.
        @web.post("/v1/audio/speech")
        async def speech(body: dict) -> Response:
            text = str(body.get("input", ""))
            voice = str(body.get("voice") or default_voice)
            # VoxCPM2 voice design: a non-"default" voice is a natural-language description
            # (gender, age, tone, pace…) the model renders from, prepended to the line in
            # parentheses. Skip if the caller already supplied their own ``(…)`` prefix.
            if voice and voice != "default" and not text.lstrip().startswith("("):
                text = f"({voice}){text}"
            wav = model.generate(
                text=text,
                cfg_value=cfg_value,
                inference_timesteps=inference_timesteps,
            )
            wav = np.asarray(wav, dtype="float32")
            buf = io.BytesIO()
            sf.write(buf, wav, sample_rate, format="WAV")
            return Response(content=buf.getvalue(), media_type="audio/wav")

        return web

    return serve


def register_all(app: modal.App, configs: Iterable[TTSModel]) -> None:
    for cfg in configs:
        register_tts_model(app, cfg)
