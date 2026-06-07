# OpenAPI / API reference

Every deployed model speaks the **OpenAI REST protocol**, so the API surface is
the familiar OpenAI one. There are two sources of truth:

- **Live, per-model spec** — each running endpoint serves its own
  auto-generated spec at `/openapi.json` and an interactive Swagger UI at
  `/docs`:

  ```
  https://<workspace>--<endpoint-name>.modal.run/openapi.json
  https://<workspace>--<endpoint-name>.modal.run/docs
  ```

- **Checked-in spec** — [`../openapi.yaml`](../openapi.yaml) documents the
  shared, stable surface across all endpoints (OpenAPI 3.1). Use it for client
  generation and review; use the live spec for the exact, version-pinned shape.

## Base URL

```
https://<workspace>--<endpoint-name>.modal.run/v1
```

One server per model; `<endpoint-name>` is the model's `endpoint_name` from
`registry.py` (e.g. `gemma-4-12b`, `nemotron-3-nano-4b`).

## Endpoints

| Method & path           | Purpose                                  |
| ----------------------- | ---------------------------------------- |
| `GET  /v1/models`       | List the model served by this endpoint.  |
| `POST /v1/chat/completions` | Chat completion (streaming via `stream: true`). |
| `POST /v1/completions`  | Text completion.                         |

Multimodal models (MiniCPM-o-4_5) accept array-style `content` parts
(`text` / `image_url` / `input_audio`) on chat messages. Models configured with
a `tool_call_parser` accept `tools` / `tool_choice`.

## Authentication

Auth is **off by default** (endpoints are public; any token is accepted). To
require a bearer token, deploy with auth enabled — secrets are supplied as
environment variables, never hard-coded:

```bash
# 1. Create the secret. The KEY must be VLLM_API_KEY (vLLM reads this env var);
#    the VALUE is the bearer token clients will send.
modal secret create llm-api-key VLLM_API_KEY=sk-your-token

# 2. Deploy with auth turned on (per provider app).
MODAL_LLM_REQUIRE_AUTH=1 modal deploy modal/app_google.py
```

With auth on, vLLM enforces `Authorization: Bearer <token>` and returns `401`
otherwise. Clients pass the same token as their API key.

## Examples

### curl

```bash
curl https://<workspace>--gemma-4-12b.modal.run/v1/chat/completions \
  -H "Authorization: Bearer $LLM_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "google/gemma-4-12B",
    "messages": [{"role": "user", "content": "Describe a mossy ticket booth."}],
    "max_tokens": 256
  }'
```

### OpenAI SDK

```python
from openai import OpenAI

client = OpenAI(
    base_url="https://<workspace>--gemma-4-12b.modal.run/v1",
    api_key=os.environ["LLM_API_KEY"],  # any value when auth is off
)
resp = client.chat.completions.create(
    model="google/gemma-4-12B",
    messages=[{"role": "user", "content": "Hello from the wood."}],
)
print(resp.choices[0].message.content)
```

The bundled [`../client.py`](../client.py) wraps this and reads the token from
the `LLM_API_KEY` environment variable.

## Generating clients

```bash
# Typed client from the checked-in spec...
openapi-generator-cli generate -i modal/openapi.yaml -g python -o ./gen

# ...or from a live endpoint's exact spec:
curl -s https://<workspace>--gemma-4-12b.modal.run/openapi.json -o openapi.json
```
