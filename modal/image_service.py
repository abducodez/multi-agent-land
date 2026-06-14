"""Image-generation serving for Modal — an OpenAI-compatible ``/v1/images/generations`` route.

Mirrors ``service.py``'s shape (one autoscaling ``@app.function`` per model, weights on the
shared Hugging Face cache volume, ``serialized=True``) but a diffusion model isn't a chat
model, so it can't ride the vLLM recipe. Instead each endpoint serves a small FastAPI ASGI
app that loads the pipeline once per container and answers the OpenAI images shape
(``{model, prompt, size, n, response_format}`` → ``{data: [{b64_json}]}``). The engine's
OpenAI SDK client (``client.images.generate``) therefore calls it unchanged.

Deploy:  uv run scripts/deploy_modal.py images --keep-warm
"""

from __future__ import annotations

from collections.abc import Iterable

import modal

from media_catalogue import CUDA_IMAGE, HF_CACHE_PATH, HF_SECRET_NAME, PYTHON_VERSION, ImageModel

hf_cache_vol = modal.Volume.from_name("huggingface-cache", create_if_missing=True)


def build_image(cfg: ImageModel) -> modal.Image:
    image = modal.Image.from_registry(CUDA_IMAGE, add_python=PYTHON_VERSION).entrypoint([])
    # A ``git+https://`` pip dep needs git in the builder, and the base CUDA image has
    # none. Add it only when a git source is present (released wheels need nothing).
    if any("git+" in pkg for pkg in cfg.extra_pip):
        image = image.apt_install("git")
    return image.uv_pip_install("torch", *cfg.extra_pip).env(
        {"HF_HUB_CACHE": HF_CACHE_PATH, "HF_XET_HIGH_PERFORMANCE": "1"}
    )


def register_image_model(app: modal.App, cfg: ImageModel) -> modal.Function:
    image = build_image(cfg)
    target_inputs = max(1, (cfg.max_concurrent_inputs * 3) // 4)
    # Gated repos (FLUX.2) need a Hugging Face token at download time.
    secrets = [modal.Secret.from_name(HF_SECRET_NAME)] if cfg.gated else []

    @app.function(
        name=cfg.endpoint_name,
        image=image,
        gpu=cfg.gpu,
        volumes={HF_CACHE_PATH: hf_cache_vol},
        secrets=secrets,
        scaledown_window=cfg.scaledown_window,
        timeout=cfg.request_timeout,
        serialized=True,
    )
    @modal.concurrent(max_inputs=cfg.max_concurrent_inputs, target_inputs=target_inputs)
    @modal.asgi_app()
    def serve():
        import base64
        import io

        import diffusers
        import torch
        from fastapi import FastAPI, Request

        # Resolve the pipeline class by name. ``DiffusionPipeline`` auto-resolves newer
        # architectures (FLUX.2) from the repo config; classic models fall back to the
        # text2image autodetector.
        pipeline_cls = getattr(diffusers, cfg.pipeline_class, diffusers.AutoPipelineForText2Image)
        dtype = getattr(torch, cfg.dtype, torch.float16)

        # Load the pipeline once per container; subsequent requests reuse it.
        pipe = pipeline_cls.from_pretrained(cfg.repo_id, torch_dtype=dtype, cache_dir=HF_CACHE_PATH)
        # CPU offload keeps peak VRAM low (only the active module is resident) so a large
        # model loads on a modest GPU; otherwise pin the whole pipeline on-device for speed.
        if cfg.cpu_offload:
            pipe.enable_model_cpu_offload()
        else:
            pipe = pipe.to("cuda")

        web = FastAPI()

        @web.get("/v1/models")
        def models() -> dict:
            return {"object": "list", "data": [{"id": cfg.repo_id, "object": "model"}]}

        @web.post("/v1/images/generations")
        async def generate(request: Request) -> dict:
            body = await request.json()
            prompt = str(body.get("prompt", ""))
            try:
                w, h = (int(x) for x in str(body.get("size", "1024x1024")).lower().split("x"))
            except Exception:
                w, h = 1024, 1024
            n = max(1, int(body.get("n", 1) or 1))
            # A distilled (CFG-free) model wants no guidance_scale at all — pass it only
            # when configured, matching the reference impl's prompt-and-steps-only call.
            kwargs: dict = {"prompt": prompt, "num_inference_steps": cfg.steps, "width": w, "height": h}
            if cfg.guidance is not None:
                kwargs["guidance_scale"] = cfg.guidance
            out: list[dict] = []
            for _ in range(n):
                img = pipe(**kwargs).images[0]
                buf = io.BytesIO()
                img.save(buf, format="PNG")
                out.append({"b64_json": base64.b64encode(buf.getvalue()).decode("ascii")})
            return {"created": 0, "data": out}

        return web

    return serve


def register_all(app: modal.App, configs: Iterable[ImageModel]) -> None:
    for cfg in configs:
        register_image_model(app, cfg)
