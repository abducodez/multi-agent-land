"""Tiny OpenAI-compatible client for smoke-testing a deployed endpoint.

Usage:
    python modal/client.py \\
        --base-url https://<workspace>--google-llms-gemma-4-12b.modal.run/v1 \\
        --model google/gemma-4-12B \\
        --prompt "Describe a mossy ticket booth in the wood."

The endpoint URL is https://<workspace>--<app-name>-<endpoint-name>.modal.run/v1,
where <app-name> is the modal.App (nvidia-llms / openbmb-llms / google-llms) and
<endpoint-name> is the per-model slug.  --model is the served model id (the HF
repo id), NOT the URL slug.

The endpoints speak the OpenAI REST API, so the official ``openai`` SDK works
unchanged — the engine reaches them via the LiteLLM gateway, binding each profile
from ``modal/catalogue.py`` + ``MODAL_WORKSPACE`` (or a single ``MODAL_LLM_BASE_URL``).
"""

from __future__ import annotations

import argparse
import os


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True, help="endpoint URL ending in /v1")
    parser.add_argument("--model", required=True, help="served model id")
    parser.add_argument("--prompt", default="Say hello in one sentence.")
    parser.add_argument("--max-tokens", type=int, default=256)
    args = parser.parse_args()

    from openai import OpenAI

    # Bearer token from the env var (set LLM_API_KEY to the value of the
    # `llm-api-key` Modal Secret). Any value works when the server has no auth.
    client = OpenAI(base_url=args.base_url, api_key=os.environ.get("LLM_API_KEY", "EMPTY"))

    response = client.chat.completions.create(
        model=args.model,
        messages=[{"role": "user", "content": args.prompt}],
        max_tokens=args.max_tokens,
    )
    print(response.choices[0].message.content)


if __name__ == "__main__":
    main()
