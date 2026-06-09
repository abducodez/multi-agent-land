# ADR-0024: A Second Inference Backend — Hugging Face Serverless

## Status

Accepted (amends ADR-0015 *LiteLLM gateway*, ADR-0019 *one catalogue / no cloud path*,
ADR-0021 *Fishbowl UI*, ADR-0022 *per-agent explicit model binding*)

## Context

Until now the engine had exactly one way to run a live cast: the OpenAI-compatible
vLLM endpoints the project deploys itself on Modal (ADR-0014/0015/0019). That backend
is great for control and cost metering, but it has a real cost of entry — you must
deploy and warm GPUs before a single token flows, and every model you want to "hook
up" is a deploy. For a hackathon where the pitch is *many small models*, we wanted a
second path with near-zero setup: pick a small instruct model and call it.

Hugging Face's **Inference Providers** expose exactly that — a serverless,
OpenAI-compatible router (`https://router.huggingface.co/v1`) where a single
`HF_TOKEN` makes a large catalogue of small models callable, no serving to operate.
Crucially this is *not* the generic-cloud path ADR-0019 removed: it is still
small-model-only (≤32B), and it routes to inference you authorise with your own token,
in keeping with the project's identity.

The constraint: ADR-0019 made `modal/catalogue.py` the single source of truth and
ADR-0022 made per-agent model binding flow through one catalogue key
(`manifest.model_endpoint`). A second backend must slot in *without* forking that
machinery or making the engine name a vendor.

## Decision

**A backend is a catalogue + a binding rule.** Add `src/models/hf_catalogue.py` — a
stdlib-only, offline-safe list of small instruct models (tiny ≤4B … strong ≤32B),
mirroring the Modal catalogue's engine-facing shape. Its `binding_for()` yields the
same LiteLLM custom-endpoint form the Modal path uses — `model = openai/<repo_id>`,
`base_url` the HF router (or `HF_INFERENCE_BASE_URL` for a self-hosted TGI / dedicated
endpoint), `api_key` the HF token — so the existing `LiteLLMProvider` transport calls
it with **zero new code**.

**One façade over both backends.** Add `src/models/inference.py`, a thin registry that
unifies Modal + HF behind `entries()` / `entry_by_key()` / `binding_for()` /
`default_key_for_profile()` / `backend_available()`. Models are named by a
*backend-qualified key* `"<backend>:<raw>"` (e.g. `hf:Qwen/Qwen2.5-7B-Instruct`); a
**bare** key with no recognised prefix means Modal, so every existing
`model_endpoint`, `config/models.yaml` `endpoint:`, and test keeps working untouched.
The router's `_catalogue_spec`, the config loader's `endpoint:` expansion, and the
live/offline gate (`has_live_credentials`) all read this one façade — adding a third
backend later touches only `inference._BACKENDS`.

**The UI makes the choice obvious and per-run.** The Fishbowl Lab gains a headline
"§00 · Inference backend" radio (Modal vs Hugging Face). It drives which catalogue the
cast/judge pickers draw from, and switching it re-seeds the picks to the new backend's
models. The selected backend rides along in each qualified key, so `collect_world_config`
needs no special-casing to bind correctly. The topbar chip now names the configured
backend(s) — `LIVE · MODAL` / `LIVE · HUGGING FACE` / `LIVE · MODAL + HF` / `OFFLINE · STUB`.

**Per-run live/offline follows the chosen backend.** A composed run sets the world's
`models.offline = False` when the selected backend has credentials, so an HF-only setup
(no Modal env) goes live on HF; with no credentials it stays auto → the deterministic
stub, so the offline demo is reproducible no matter which backend is selected.

## Consequences

- Hooking up a new small model on HF is a one-line `HFModel(...)` append — no deploy,
  no GPU, instantly bindable by the cast and offerable in the Lab.
- The engine still never names a vendor on a hot path: routing is by qualified key
  through one façade; the transport (`LiteLLMProvider`) is unchanged.
- Backward compatible by construction: bare keys = Modal, so existing config, manifests,
  and the 256-test green baseline are unaffected (the suite grows, not breaks).
- HF model availability on the serverless router shifts over time; because the catalogue
  is plain data, retuning the list is a one-line edit — no engine change.
- `has_live_credentials()` now means "*any* backend is configured," so a stray `HF_TOKEN`
  in the environment activates the live path; tests that assert offline clear the HF env
  alongside the Modal env to stay hermetic.
- The offline stub remains the default with no credentials, no network, and the optional
  extras uninstalled.
