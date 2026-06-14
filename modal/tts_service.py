"""Text-to-speech serving for Modal — an OpenAI-compatible ``/v1/audio/speech`` route.

Same shape as ``image_service.py``: one autoscaling ``@app.function`` per model serving a
small FastAPI ASGI app that loads the TTS pipeline once per container and answers the
OpenAI speech shape (``{model, input, voice, response_format}`` → WAV bytes). The engine's
OpenAI SDK client (``client.audio.speech.create``) calls it unchanged. The model is ~82M
params — the ≤4B "Tiny Titan" story — and runs comfortably on a small GPU.

Deploy:  uv run scripts/deploy_modal.py tts --keep-warm
"""

from __future__ import annotations

from collections.abc import Iterable

import modal

from media_catalogue import CUDA_IMAGE, HF_CACHE_PATH, PYTHON_VERSION, TTSModel

hf_cache_vol = modal.Volume.from_name("huggingface-cache", create_if_missing=True)


def build_image(cfg: TTSModel) -> modal.Image:
    # Kokoro pulls misaki[en] → spacy-curated-transformers → curated-tokenizers, which
    # compiles a bundled sentencepiece from C++ source at install time. Modal's add_python
    # uses a python-build-standalone interpreter whose sysconfig compiler is clang/clang++,
    # so the build needs clang (+ a C/C++ toolchain) on PATH — the CUDA image ships neither
    # by default (the "clang++: No such file or directory" build failure). espeak-ng is
    # Kokoro's runtime grapheme-to-phoneme fallback for out-of-vocabulary words.
    return (
        modal.Image.from_registry(CUDA_IMAGE, add_python=PYTHON_VERSION)
        .entrypoint([])
        .apt_install("espeak-ng", "clang", "build-essential")
        .uv_pip_install("torch", *cfg.extra_pip)
        .env({"HF_HUB_CACHE": HF_CACHE_PATH, "HF_XET_HIGH_PERFORMANCE": "1"})
    )


def register_tts_model(app: modal.App, cfg: TTSModel) -> modal.Function:
    image = build_image(cfg)
    target_inputs = max(1, (cfg.max_concurrent_inputs * 3) // 4)

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
        from fastapi import FastAPI, Request, Response
        from kokoro import KPipeline

        pipeline = KPipeline(lang_code=cfg.lang_code)

        web = FastAPI()

        @web.get("/v1/models")
        def models() -> dict:
            return {"object": "list", "data": [{"id": cfg.repo_id, "object": "model"}]}

        @web.post("/v1/audio/speech")
        async def speech(request: Request) -> Response:
            body = await request.json()
            text = str(body.get("input", ""))
            voice = str(body.get("voice") or cfg.default_voice)
            if voice == "default":
                voice = cfg.default_voice
            chunks = [audio for _, _, audio in pipeline(text, voice=voice)]
            wav = np.concatenate(chunks) if chunks else np.zeros(1, dtype="float32")
            buf = io.BytesIO()
            sf.write(buf, wav, cfg.sample_rate, format="WAV")
            return Response(content=buf.getvalue(), media_type="audio/wav")

        return web

    return serve


def register_all(app: modal.App, configs: Iterable[TTSModel]) -> None:
    for cfg in configs:
        register_tts_model(app, cfg)
