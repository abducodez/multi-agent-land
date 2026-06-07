# ADR-0005: Serve Small Models on Modal, One App Per Provider

## Status

Accepted

## Context

The engine routes agent roles to small models through an OpenAI-compatible
interface, but until now there was no hosted backend behind it — only the local
deterministic stub. We need real small models (all under the 32B cap, with a
≤4B Tiny Titan tier) served as APIs the engine can call, without coupling the
engine to any single inference vendor.

We want the serving layer to be scalable (autoscaling, pay-per-use), extensible
(adding a model or provider should be trivial), and configurable per task (GPU,
context length, concurrency, tool/reasoning parsers, multimodal limits).

## Decision

Add a `modal/` folder that serves models on Modal as serverless,
OpenAI-compatible endpoints (vLLM behind an autoscaling web server).

- **One Modal app per provider** (`nvidia-llms`, `openbmb-llms`, `google-llms`).
  Providers deploy, scale, and fail independently.
- **One reusable serving path** in `service.py` (`ModelConfig` + `register_model`)
  shared by every app, so the vLLM/Modal best practices are written once.
- **Configuration is data** in `registry.py`: a model is one `ModelConfig`; a
  provider is one app file. This mirrors the project's "config, not code"
  invariant (see ADR for declarative worlds).
- Weights and the vLLM compile cache live in **shared Volumes**, so a model
  pulled once is warm across every provider app.

## Consequences

- The engine talks to any endpoint via the OpenAI SDK by setting
  `OPENAI_BASE_URL`; model roles (`MODEL_TINY/FAST/BALANCED/STRONG`) map to the
  endpoint whose size fits the role.
- Vendor isolation: a provider can be added, retuned, or removed without
  touching the others or the engine.
- Gated repos (Gemma, the Nemotron repos used here) require a Hugging Face token
  in the `huggingface-secret` Modal Secret; ungated models deploy without it.
- vLLM tool/reasoning parser names are version-specific and left conservative;
  enable per model once verified against the deployed vLLM version.
- Modal's docs index is mirrored at `modal/docs/modal-llms.txt` and refreshed
  when the pinned vLLM/Modal versions change (ADR-0004, document-as-we-build).
