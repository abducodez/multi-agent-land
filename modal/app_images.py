"""Image-generation serving app (FLUX.2 [klein] 4B text-to-image).

Deploy:    modal deploy modal/app_images.py   (or: uv run scripts/deploy_modal.py images)
Serve dev: modal serve modal/app_images.py

Each model in the media catalogue gets its own OpenAI-compatible
``/v1/images/generations`` endpoint at ``…--image-gen-<endpoint>.modal.run``.
"""

from __future__ import annotations

import modal

from image_service import register_all
from media_catalogue import IMAGE_APP, IMAGE_MODELS

app = modal.App(IMAGE_APP)

register_all(app, IMAGE_MODELS)
