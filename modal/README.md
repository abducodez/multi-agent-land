# Modal model serving

Serverless, OpenAI-compatible endpoints for small models, deployed on
[Modal](https://modal.com). Each provider is an isolated Modal app; all of them
share one battle-tested serving path (vLLM behind an autoscaling web server) so
adding a model is a one-line config change.

## Layout

```text
modal/
  catalogue.py      SINGLE SOURCE OF TRUTH (stdlib-only): ModelConfig + the
                    per-provider model lists + PROVIDERS (app names) + URL helpers.
                    Shared with the engine, which reads it by path.
  service.py        Reusable serving layer: image + vllm command, register_model()
                    (provider-agnostic). Imports ModelConfig from catalogue.
  registry.py       Back-compat re-export of the catalogue's model lists.
  app_nvidia.py     App "nvidia-llms"  — Nemotron 3 Nano 4B + 30B, Cascade 14B Thinking.
  app_openbmb.py    App "openbmb-llms" — MiniCPM4.1-8B + MiniCPM-o 4.5.
  app_google.py     App "google-llms"  — Gemma 4 12B + 26B.
  vllm_logging.py   Dependency-free JSON log formatter shipped into the image
                    when MODAL_LLM_JSON_LOGS=1 (structured logs via vLLM dictConfig).
  client.py         OpenAI-SDK smoke-test client for any endpoint.
  openapi.yaml      Checked-in OpenAPI 3.1 spec for the served API surface.
  pyproject.toml    uv workspace member (deploy/client tooling; non-package).
  requirements.txt  Deploy/client tooling (vLLM lives in the container image).
  docs/
    deploying.md    Deploy, configure, auth, GPU sizing, engine integration.
    openapi.md      API reference: endpoints, auth, examples, client generation.
    modal-llms.txt  In-repo mirror of Modal's docs index, kept updated.
```

Each running endpoint also self-documents at `/docs` (Swagger UI) and
`/openapi.json` (live spec). See [`docs/openapi.md`](docs/openapi.md).

## Models

| Provider | App            | Model                                   | Endpoint name         | GPU     |
| -------- | -------------- | --------------------------------------- | --------------------- | ------- |
| NVIDIA   | `nvidia-llms`  | NVIDIA-Nemotron-3-Nano-30B-A3B-BF16     | `nemotron-3-nano-30b` | H200:1  |
| NVIDIA   | `nvidia-llms`  | Nemotron-Cascade-14B-Thinking           | `nemotron-cascade-14b`          | L40S:1  |
| NVIDIA   | `nvidia-llms`  | NVIDIA-Nemotron-3-Nano-4B-BF16          | `nemotron-3-nano-4b`  | L4:1    |
| OpenBMB  | `openbmb-llms` | MiniCPM-o-4_5 (omni)                    | `minicpm-o-4-5`       | L40S:1  |
| OpenBMB  | `openbmb-llms` | MiniCPM4.1-8B                           | `minicpm-4-1-8b`      | L40S:1  |
| Google   | `google-llms`  | gemma-4-26B-A4B-it                      | `gemma-4-26b`         | H200:1  |
| Google   | `google-llms`  | gemma-4-12B                             | `gemma-4-12b`         | L40S:1  |

Every endpoint stays under the hackathon's 32B cap; `nemotron-3-nano-4b` is the
≤4B Tiny Titan tier.

## Quick start

```bash
pip install -r modal/requirements.txt
modal token new
modal secret create huggingface-secret HF_TOKEN=hf_xxx   # for gated repos

modal deploy modal/app_nvidia.py
python modal/client.py \
  --base-url https://<workspace>--nvidia-llms-nemotron-3-nano-4b.modal.run/v1 \
  --model nvidia/NVIDIA-Nemotron-3-Nano-4B-BF16 \
  --prompt "Hello from the wood."
```

See [`docs/deploying.md`](docs/deploying.md) for configuration, auth, GPU
sizing, and how to add models/providers or wire endpoints into the engine.

## Why this shape

- **Each provider in its own app** — independent deploy, scaling, and blast
  radius; one provider's outage or redeploy never touches another.
- **Scalable** — serverless autoscaling, input concurrency, a shared weight
  cache (pull once, warm everywhere), and per-model `min_containers` warm pools.
- **Extensible** — add a model = one `ModelConfig` in `catalogue.py`; add a
  provider = one `Provider` entry + one app file. The serving path is written once
  in `service.py`, and the engine picks up the new model with no edits (it reads
  the same `catalogue.py`).
- **Configurable per task** — GPU, context length, concurrency, tool/reasoning
  parsers, and multimodal limits are all data in `catalogue.py`.
- **One source of truth** — `catalogue.py` describes every model once; both the
  serving apps and the engine read it, so the served id and endpoint URL never
  drift between deploy and call sites (ADR-0019).
