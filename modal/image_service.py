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

from media_catalogue import CUDA_IMAGE, HF_CACHE_PATH, PYTHON_VERSION, ImageModel

hf_cache_vol = modal.Volume.from_name("huggingface-cache", create_if_missing=True)


def build_image(cfg: ImageModel) -> modal.Image:
    return (
        modal.Image.from_registry(CUDA_IMAGE, add_python=PYTHON_VERSION)
        .entrypoint([])
        .uv_pip_install("torch", *cfg.extra_pip)
        .env({"HF_HUB_CACHE": HF_CACHE_PATH, "HF_XET_HIGH_PERFORMANCE": "1"})
    )


def register_image_model(app: modal.App, cfg: ImageModel) -> modal.Function:
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
        import base64
        import io

        import torch
        from diffusers import AutoPipelineForText2Image
        from fastapi import FastAPI, Request

        # Load the pipeline once per container; subsequent requests reuse it.
        pipe = AutoPipelineForText2Image.from_pretrained(
            cfg.repo_id, torch_dtype=torch.float16, cache_dir=HF_CACHE_PATH
        ).to("cuda")

        web = FastAPI()

        @web.get("/v1/models")
        def models() -> dict:
            return {"object": "list", "data": [{"id": cfg.repo_id, "object": "model"}]}

        @web.post("/v1/images/generations")
        async def generate(request: Request) -> dict:
            body = await request.json()
            prompt = str(body.get("prompt", ""))
            try:
                w, h = (int(x) for x in str(body.get("size", "512x512")).lower().split("x"))
            except Exception:
                w, h = 512, 512
            n = max(1, int(body.get("n", 1) or 1))
            out: list[dict] = []
            for _ in range(n):
                img = pipe(
                    prompt=prompt,
                    num_inference_steps=cfg.steps,
                    guidance_scale=cfg.guidance,
                    width=w,
                    height=h,
                ).images[0]
                buf = io.BytesIO()
                img.save(buf, format="PNG")
                out.append({"b64_json": base64.b64encode(buf.getvalue()).decode("ascii")})
            return {"created": 0, "data": out}

        return web

    return serve


def register_all(app: modal.App, configs: Iterable[ImageModel]) -> None:
    for cfg in configs:
        register_image_model(app, cfg)
