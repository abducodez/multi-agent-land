"""Tiny OpenAI-compatible client for smoke-testing a deployed endpoint.

Usage:
    python modal/client.py \\
        --base-url https://<workspace>--gemma-4-12b.modal.run/v1 \\
        --model google/gemma-4-12B \\
        --prompt "Describe a mossy ticket booth in the wood."

The endpoints speak the OpenAI REST API, so the official ``openai`` SDK works
unchanged — the engine can point ``OPENAI_BASE_URL`` at any of these URLs.
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

    # vLLM accepts any token unless an API key was configured on the server.
    client = OpenAI(base_url=args.base_url, api_key=os.environ.get("MODAL_LLM_KEY", "EMPTY"))

    response = client.chat.completions.create(
        model=args.model,
        messages=[{"role": "user", "content": args.prompt}],
        max_tokens=args.max_tokens,
    )
    print(response.choices[0].message.content)


if __name__ == "__main__":
    main()
