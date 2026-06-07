# Modal model serving

Serverless, OpenAI-compatible endpoints for small models, deployed on
[Modal](https://modal.com). Each provider is an isolated Modal app; all of them
share one battle-tested serving path (vLLM behind an autoscaling web server) so
adding a model is a one-line config change.

## Layout

```text
modal/
  service.py        Reusable serving layer: ModelConfig, image + vllm command,
                    register_model() (provider-agnostic).
  registry.py       Declarative catalogue of every model, grouped by provider.
  app_nvidia.py     App "nvidia-llms"  — Nemotron 3 Nano 30B + 4B.
  app_openbmb.py    App "openbmb-llms" — MiniCPM-o 4.5 + MiniCPM4.1-8B.
  app_google.py     App "google-llms"  — Gemma 4 26B + 12B.
  client.py         OpenAI-SDK smoke-test client for any endpoint.
  requirements.txt  Deploy/client tooling (vLLM lives in the container image).
  docs/
    deploying.md    Deploy, configure, auth, GPU sizing, engine integration.
    modal-llms.txt  In-repo mirror of Modal's docs index, kept updated.
```

## Models

| Provider | App            | Model                                   | Endpoint name         | GPU     |
| -------- | -------------- | --------------------------------------- | --------------------- | ------- |
| NVIDIA   | `nvidia-llms`  | NVIDIA-Nemotron-3-Nano-30B-A3B-BF16     | `nemotron-3-nano-30b` | H200:1  |
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
  --base-url https://<workspace>--nemotron-3-nano-4b.modal.run/v1 \
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
- **Extensible** — add a model = one `ModelConfig`; add a provider = one app
  file. The serving path is written once in `service.py`.
- **Configurable per task** — GPU, context length, concurrency, tool/reasoning
  parsers, and multimodal limits are all data in `registry.py`.
