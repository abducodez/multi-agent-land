# Runbook: Going Live

How to take the Fishbowl theater off the deterministic stub and onto real,
small-model inference — and why a live run stays bounded and won't loop forever.

Everything below is driven by **environment variables**. The canonical list, with
inline notes, is [`.env.example`](../.env.example) — copy it to `.env` (already
gitignored) and uncomment what you need. This runbook explains *which* knobs to
turn and in *what order*; it does not duplicate the file.

---

## The offline default

With **no environment variables set**, the app runs on a **deterministic local
stub**:

- No API keys, no network, fully reproducible.
- The in-memory ledger is used (nothing is persisted).
- Ideal for CI, demos that must replay identically, and the first 30 seconds on
  stage.

```bash
uv run app.py        # offline stub — no .env needed
```

This is the path the test suite exercises, and it must always keep working.

---

## Real vs stub

The same engine produces two very different conversations:

| | Stub (no creds) | Live (creds set) |
|---|---|---|
| Where the words come from | a **deterministic script** baked into the stub model | **genuine model output** from your served small models |
| Run-to-run | identical every time | varies — the models actually think |
| Cost | none | metered per call into the Governor budget |
| Topbar | OFFLINE-FIRST | **LIVE** |

If the conversation reads the same on every run, you are still on the stub — the
"preloaded script." Once credentials activate the live path, each run is fresh
model output. The flip is decided by `has_live_credentials()` in
[`src/models/openai_compat.py`](../src/models/openai_compat.py).

---

## Going live with Modal models

The live models are the OpenAI-compatible small models you deploy yourself on
Modal (see [`modal/README.md`](../modal/README.md) and
[`docs/architecture/model-routing.md`](architecture/model-routing.md)). There is
no generic cloud key — live inference is always against models you serve.

### Option A — workspace + key (recommended)

Set your Modal workspace; the endpoint URL is **derived**, so the workspace is the
only deploy-specific value:

```
https://${MODAL_WORKSPACE}--<app>-<endpoint>.modal.run/v1
```

```ini
# .env
MODAL_WORKSPACE=your-modal-workspace
MODAL_LLM_KEY=EMPTY        # a self-served vLLM endpoint accepts any token
```

Each logical profile (`tiny`/`fast`/`balanced`/`strong`) binds to a model by its
catalogue key in `config/models.yaml` (source of truth: `modal/catalogue.py`).

### Option B — one explicit endpoint

Point every profile at a single OpenAI-compatible base URL (one Modal-served
model, or any other OpenAI-compatible endpoint):

```ini
# .env
MODAL_LLM_BASE_URL=https://your-workspace--google-llms-gemma-4-12b.modal.run/v1
```

### Option C — Local GPU (in-process transformers)

Run inference in-process on the host's own GPU — no server to launch, no token.
The engine uses `LocalTransformersProvider` behind a `@spaces.GPU` function,
which works on ZeroGPU Spaces, dedicated-GPU Spaces (T4/L4/L40S/A100), and local
CUDA boxes. On a CPU-only host the call is a no-op and the stub remains active.

**On a CUDA box or dedicated-GPU Space:**

```ini
# .env
LOCAL_INFERENCE=1
```

Then pick **"Local GPU"** in the Lab's backend radio. On a ZeroGPU Space,
`SPACES_ZERO_GPU` is set automatically — no `.env` change needed, just select the
backend in the UI. See
[`docs/architecture/model-routing.md`](architecture/model-routing.md) for the
full model list and per-tier config syntax (`local:<repo_id>`).

### Per-profile overrides

Highest priority. Override the model string bound to any profile — the cheapest
way to put **different sponsor models in one cast**. Every model must be ≤32B
(tiny ≤4B); values are LiteLLM model strings (`openai/<served_model_id>` for a
custom endpoint):

```ini
# .env
MODEL_TINY=openai/nvidia/NVIDIA-Nemotron-3-Nano-4B-BF16
MODEL_FAST=openai/openbmb/MiniCPM4.1-8B
MODEL_BALANCED=openai/google/gemma-4-12B
MODEL_STRONG=openai/google/gemma-4-26B-A4B-it
```

Setting either `MODAL_WORKSPACE` or `MODAL_LLM_BASE_URL` activates the live path.

---

## The ledger: Neon / Postgres

By default the **in-memory** ledger is used (offline, nothing persisted). Set
`DATABASE_URL` to persist the append-only event log so a killed run can
`restore()`. Details in
[`docs/architecture/persistence.md`](architecture/persistence.md).

Managed Postgres (Neon):

```ini
# .env
DATABASE_URL=postgresql+psycopg://USER:PASSWORD@HOST/DB?sslmode=require
```

Local SQLite fallback — try the durable backend without a server:

```ini
# .env
DATABASE_URL=sqlite:///runs/events.db
```

The backend is selected in
[`src/core/ledger_factory.py`](../src/core/ledger_factory.py).

---

## The memory index: mem0

Optional semantic memory lens over the ledger (see
[`docs/architecture/memory-stack.md`](architecture/memory-stack.md) and
[`src/core/memory_index.py`](../src/core/memory_index.py)). Two backends:

**Local (off-the-grid, default when enabled).** Embeddings run on your machine via
sentence-transformers — no API key, fully offline once the model is cached
(`uv sync --extra memory`):

```ini
# .env
MEMORY_INDEX=1
```

**Cloud (hosted mem0, opt-in).** Uses mem0's managed service instead of the local
embedder:

```ini
# .env
MEMORY_INDEX=cloud
MEM0_API_KEY=m0-...
```

> **Data-egress caveat:** the cloud backend **sends ledger event text to mem0's
> servers** — a deliberate departure from the off-the-grid default. Keep the local
> backend (`MEMORY_INDEX=1`) unless you specifically want the hosted index.

---

## Budget safety: live runs are bounded

A live run **cannot loop forever**. Two independent guards enforce this:

1. **The Governor** caps every run from config. Each scenario in
   `config/scenarios/*.yaml` declares a `governor:` block (`max_turns`,
   `max_calls_per_turn`, `max_total_calls`, and — for live cost — token and
   `hourly_budget_usd` limits). Real per-call cost from the live endpoint is
   metered into this budget. See
   [ADR-0013](adr/0013-token-governor-and-long-running.md) and
   [ADR-0007](adr/0007-governor-as-runtime-safety-valve.md).
2. **The UI auto-stops** the autoplay loop when the run hits its budget or a
   **verdict** lands — the timer goes inactive on its own.

**Recommended first live run:**

1. Set credentials, restart the app.
2. **Step manually first** (⏭) for a few turns to confirm real output and watch
   the meters move.
3. Only then enable **autoplay** (▶) — and only with the governor caps in place.

If you tighten the caps, do it in the scenario YAML, not in code.

---

## Verify it's live

After setting credentials and restarting `uv run app.py`:

- The **topbar shows `LIVE`** (not `OFFLINE-FIRST`).
- The **meters show real tokens and spend climbing** as turns run — the token
  meter prefers the governor's real `total_tokens`, so on the stub it stays at the
  estimate while live runs tick up actual usage.
- The conversation **changes between runs** from the same seed.

If tokens/spend stay flat and the dialogue is identical each run, you are still on
the stub — recheck that `MODAL_WORKSPACE` (or `MODAL_LLM_BASE_URL`) is set in the
`.env` the app actually loaded.
