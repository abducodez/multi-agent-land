# ADR-0019: One Model Catalogue, No Cloud-Model Path, Local Embeddings

## Status

Accepted (amends ADR-0010, ADR-0014 *Modal model serving*, ADR-0015, ADR-0018)

## Context

The model layer had grown two parallel descriptions of the same facts:

- **`modal/registry.py`** — what is *deployed*: each model's serving `ModelConfig`
  (HF repo id, GPU, vLLM flags), grouped per provider app.
- **`config/models.yaml`** — what the engine *calls*: each profile's
  `openai/<served_id>` model string and the full endpoint URL
  `https://${MODAL_WORKSPACE}--<app>-<endpoint>.modal.run/v1`.

The served id and the endpoint slug appeared verbatim in both. Adding or renaming
a model meant editing the catalogue *and* hand-mirroring the slug/URL/id into the
YAML, with nothing connecting them — exactly the kind of drift that bites during a
live demo. The folder is named `modal`, which shadows the PyPI `modal` SDK, so the
engine could not simply `import` the catalogue to share it.

Two further cleanups were due. (1) ADR-0015 kept `OPENAI_API_KEY` as a live-path
activation signal and the per-tier defaults (`resolve_model`) were `gpt-4o-*`; the
project's identity is "small models you deploy yourself," so a generic cloud path
is dead weight and a footgun. (2) The optional semantic memory index (ADR-0018)
embedded via `OPENAI_API_KEY` by default — the last cloud dependency on an
otherwise off-the-grid engine.

## Decision

**One catalogue, stdlib-only, shared by both sides.** Move the model catalogue to
`modal/catalogue.py` — `ModelConfig`, the per-provider model lists, a
`Provider`/`PROVIDERS` map that pairs each app name with its models, and the
`endpoint_url()` / `litellm_model()` / `entries()` helpers. It imports nothing but
the stdlib (no `import modal`). The serving layer (`service.py`, `app_<provider>.py`)
consumes it; `registry.py` stays as a thin back-compat re-export.

**The engine reads the same catalogue, by path.** `src/models/modal_catalogue.py`
loads `modal/catalogue.py` from its file under a non-conflicting module name
(sidestepping the `modal` SDK clash), and derives each profile's binding —
`model = openai/<served_id>`, `base_url` from `$MODAL_WORKSPACE` (or
`$MODAL_LLM_BASE_URL`), `api_key` from `$MODAL_LLM_KEY`. The load is cached,
dependency-free, offline-safe, and degrades to "no catalogue" if the file is
absent.

**`config/models.yaml` binds by catalogue key.** A profile names an `endpoint:`
(the catalogue slug) instead of spelling out the model string and URL.
`Registry.from_dir()` expands that key into the concrete binding (honouring a
`MODEL_<PROFILE>` env override) before validation. Adding a model is now a
one-line edit in `modal/catalogue.py`; pointing a tier at a different model is a
one-line `endpoint:` change. Nothing duplicates the served id or URL. Each model
also carries a `profile` tier tag, so the catalogue self-describes its default
casting.

**No cloud-model path.** `has_live_credentials()` activates the live path on
`MODAL_WORKSPACE` / `MODAL_LLM_BASE_URL` only — `OPENAI_API_KEY` is no longer a
model signal. `resolve_model()`'s per-tier defaults come from the catalogue (the
Modal served ids), not `gpt-4o-*`, and the `MODEL_NAME` catch-all is gone (the
per-tier `MODEL_<PROFILE>` overrides remain). The generic `OpenAICompatProvider`
is retained for the legacy `build_from_env()` path and as the role→persona home,
now defaulting to the Modal binding.

**Local embeddings.** The memory index (ADR-0018) defaults to a local
sentence-transformers embedder (`sentence-transformers/all-MiniLM-L6-v2`) with no
API key; events are still stored verbatim (`infer=False`), so mem0's generative
LLM is never invoked. `sentence-transformers` joins the `memory` extra.
`MEMORY_INDEX_CONFIG` still overrides the whole config.

## Consequences

- The catalogue is the single source of truth: a model added in
  `modal/catalogue.py` is deployable *and* immediately bindable by the engine,
  with no parallel edit. This is the "modular, scalable, include-a-model-in-one-place"
  property the hackathon design promised.
- The engine never imports the `modal` SDK to read the catalogue — the by-path load
  keeps the offline path light and the SDK-name clash a non-issue.
- The whole engine is off the grid by default: stub models offline, local
  embeddings when the index is on, and live inference only against endpoints you
  deploy. (Reinforces the *Off the Grid* badge.)
- The offline stub remains the default and the test suite stays green with no
  credentials, no network, and the optional extras uninstalled.
- A by-path import of a sibling file is slightly unusual; it is justified by the
  folder-name clash and contained to one well-documented module. If `modal/` is
  ever published as a real package, this collapses to a normal import.
- Old ADRs are left as historical records; this ADR amends their `OPENAI_API_KEY`
  / `registry.py` / `gpt-4o-*` specifics.
