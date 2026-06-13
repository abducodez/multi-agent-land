"""Concurrent health-check for every Modal-served LLM endpoint.

The endpoints are serverless GPU containers with ``min_containers=0``, so the
first call to a cold model pays a full cold start (weight download → container
boot → vLLM load → CUDA-graph capture). Testing them one at a time pays that cost
N times back to back. This script instead **fans every endpoint out at once**, so
the wall-clock is roughly the slowest single cold start, not the sum.

For each endpoint it runs two phases:
  * liveness  — ``GET /v1/models`` (retried with backoff while the container is
    still booting; connection errors / 502 / 503 mean "still warming", not "dead");
  * inference — one tiny ``POST /v1/chat/completions`` (max_tokens=8) to prove the
    model actually generates.

It prints a live, per-endpoint progress board while it waits, then a final table.

Usage::

    uv run python modal/healthcheck.py                  # all endpoints, concurrent
    uv run python modal/healthcheck.py --profiles-only  # just the 4 engine tiers
    uv run python modal/healthcheck.py --print-urls      # resolve URLs and exit
    uv run python modal/healthcheck.py --no-chat         # liveness only
    uv run python modal/healthcheck.py --only gemma-4-12b,minicpm-4-1-8b

Workspace resolution (first hit wins): ``--workspace`` → ``$MODAL_WORKSPACE`` →
``modal profile current``. A single-endpoint override via ``$MODAL_LLM_BASE_URL``
is honoured when set. The bearer token comes from ``$MODAL_LLM_KEY`` /
``$LLM_API_KEY`` and defaults to ``"EMPTY"`` (the endpoints are public unless
deployed with auth).
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType

import httpx

HERE = Path(__file__).resolve().parent
CATALOGUE_PATH = HERE / "catalogue.py"

# Phases the engine actually casts (catalogue ModelConfig.profile values).
PROFILE_TIERS = {"tiny", "fast", "balanced", "strong"}


def load_catalogue() -> ModuleType:
    """Load ``catalogue.py`` by path (mirrors ``src/models/modal_catalogue.py``)."""
    spec = importlib.util.spec_from_file_location("modal_catalogue_local", CATALOGUE_PATH)
    if spec is None or spec.loader is None:  # pragma: no cover - defensive
        raise RuntimeError(f"cannot load catalogue from {CATALOGUE_PATH}")
    module = importlib.util.module_from_spec(spec)
    # Register before exec: dataclass() resolves cls.__module__ via sys.modules.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def resolve_workspace(cli_value: str | None) -> str | None:
    """First hit wins: --workspace, $MODAL_WORKSPACE, then `modal profile current`."""
    if cli_value:
        return cli_value
    env = os.environ.get("MODAL_WORKSPACE")
    if env:
        return env
    try:
        out = subprocess.run(
            ["modal", "profile", "current"],
            capture_output=True,
            text=True,
            timeout=20,
            check=True,
        )
        slug = out.stdout.strip()
        return slug or None
    except (subprocess.SubprocessError, FileNotFoundError, OSError):
        return None


@dataclass
class Target:
    """One endpoint under test plus its mutable live status."""

    key: str
    app: str
    served_model_id: str
    profile: str | None
    params_b: float | None
    base_url: str

    # live status, mutated as the check runs
    phase: str = "queued"
    models_ok: bool | None = None
    chat_ok: bool | None = None
    served_reported: str | None = None
    finish_reason: str | None = None
    error: str | None = None
    attempts: int = 0
    started: float = 0.0
    elapsed: float = 0.0
    sample: str | None = None
    history: list[str] = field(default_factory=list)

    def note(self, phase: str) -> None:
        self.phase = phase


# 5xx + Modal's cold-start signals. 303/307/308 are normally followed by the
# client (follow_redirects=True); kept here so an exhausted redirect chain
# (TooManyRedirects → caller re-issues) is still treated as "still warming".
RETRYABLE_STATUS = {303, 307, 308, 408, 425, 429, 500, 502, 503, 504}


async def check_models(client: httpx.AsyncClient, t: Target, deadline: float) -> None:
    """Poll GET /v1/models until the server answers 200 or the deadline passes."""
    backoff = 2.0
    while True:
        t.attempts += 1
        t.note("booting" if t.attempts > 1 else "connecting")
        try:
            r = await client.get(f"{t.base_url}/models")
            if r.status_code == 200:
                data = r.json()
                ids = [m.get("id") for m in data.get("data", [])]
                t.served_reported = ", ".join(i for i in ids if i) or None
                t.models_ok = True
                t.note("models ok")
                return
            if r.status_code == 401:
                t.models_ok = False
                t.error = "401 unauthorized (endpoint requires a bearer token)"
                t.note("auth error")
                return
            if r.status_code not in RETRYABLE_STATUS:
                t.models_ok = False
                t.error = f"GET /models -> HTTP {r.status_code}: {r.text[:160]}"
                t.note("http error")
                return
            # retryable: container still warming
        except httpx.TooManyRedirects:
            # Modal kept returning 303 (still cold-starting) past the redirect cap;
            # re-issue the request for a fresh redirect budget.
            t.error = "still cold-starting (redirect chain exhausted)"
        except (httpx.ConnectError, httpx.ReadError, httpx.RemoteProtocolError) as exc:
            t.error = f"{type(exc).__name__}: {exc}"[:160]
        except httpx.TimeoutException:
            t.error = "request timeout while warming"

        if time.monotonic() >= deadline:
            t.models_ok = False
            t.note("timed out")
            if not t.error:
                t.error = "deadline reached before /models answered"
            return
        await asyncio.sleep(min(backoff, max(1.0, deadline - time.monotonic())))
        backoff = min(backoff * 1.5, 20.0)


async def check_chat(client: httpx.AsyncClient, t: Target, deadline: float) -> None:
    """One small chat completion to prove the model generates."""
    payload = {
        "model": t.served_model_id,
        "messages": [{"role": "user", "content": "ping"}],
        "max_tokens": 8,
        "temperature": 0.0,
    }
    backoff = 2.0
    while True:
        t.note("generating")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            t.chat_ok = False
            t.note("timed out")
            t.error = t.error or "deadline reached before chat completed"
            return
        try:
            r = await client.post(
                f"{t.base_url}/chat/completions",
                json=payload,
                timeout=httpx.Timeout(min(remaining, 300.0), connect=30.0),
            )
            if r.status_code == 200:
                data = r.json()
                choice = (data.get("choices") or [{}])[0]
                msg = (choice.get("message") or {}).get("content") or ""
                t.finish_reason = choice.get("finish_reason")
                t.sample = " ".join(msg.split())[:80] or "(empty)"
                t.chat_ok = True
                t.note("done")
                return
            if r.status_code not in RETRYABLE_STATUS:
                t.chat_ok = False
                t.error = f"POST /chat/completions -> HTTP {r.status_code}: {r.text[:200]}"
                t.note("chat error")
                return
        except httpx.TooManyRedirects:
            # Modal kept returning 303 (still cold-starting) past the redirect cap;
            # re-issue the request for a fresh redirect budget.
            t.error = "still cold-starting (redirect chain exhausted)"
        except (httpx.ConnectError, httpx.ReadError, httpx.RemoteProtocolError) as exc:
            t.error = f"{type(exc).__name__}: {exc}"[:160]
        except httpx.TimeoutException:
            t.error = "chat request timed out"

        if time.monotonic() >= deadline:
            t.chat_ok = False
            t.note("timed out")
            return
        await asyncio.sleep(min(backoff, max(1.0, deadline - time.monotonic())))
        backoff = min(backoff * 1.5, 20.0)


async def run_target(t: Target, api_key: str, timeout: int, do_chat: bool, sem: asyncio.Semaphore) -> None:
    async with sem:
        t.started = time.monotonic()
        deadline = t.started + timeout
        headers = {"Authorization": f"Bearer {api_key}"}
        limits = httpx.Limits(max_connections=4)
        # Generous read timeout: a cold container can take minutes to reply.
        client_timeout = httpx.Timeout(timeout, connect=30.0)
        # CRITICAL: follow redirects. A Modal web endpoint that hasn't responded
        # within 150s returns a 303 to the same URL (clients are expected to follow
        # it — up to ~20 hops / 50 min) while the container finishes cold-starting.
        # Without this, the first 303 at ~150s looks like a terminal error.
        async with httpx.AsyncClient(
            headers=headers, timeout=client_timeout, limits=limits, follow_redirects=True, max_redirects=20
        ) as client:
            await check_models(client, t, deadline)
            if t.models_ok and do_chat:
                await check_chat(client, t, deadline)
        t.elapsed = time.monotonic() - t.started


# --- live progress board -------------------------------------------------------

PHASE_ICON = {
    "queued": "·",
    "connecting": "◌",
    "booting": "◍",
    "models ok": "◉",
    "generating": "◍",
    "done": "✅",
    "timed out": "⏰",
    "http error": "❌",
    "auth error": "🔒",
    "chat error": "❌",
}


def render_board(targets: list[Target], started: float) -> str:
    width = max(len(t.key) for t in targets)
    lines = [f"  cold-start health-check · {len(targets)} endpoints · {time.monotonic() - started:5.0f}s elapsed"]
    for t in targets:
        live = t.elapsed or (time.monotonic() - t.started if t.started else 0.0)
        icon = PHASE_ICON.get(t.phase, "?")
        detail = t.phase
        if t.phase == "booting":
            detail = f"booting (try {t.attempts})"
        lines.append(f"  {icon} {t.key:<{width}}  {live:6.0f}s  {detail}")
    return "\n".join(lines)


async def progress_loop(targets: list[Target], started: float, done: asyncio.Event) -> None:
    prev_lines = 0
    while not done.is_set():
        board = render_board(targets, started)
        if prev_lines:
            sys.stdout.write(f"\x1b[{prev_lines}A")
        sys.stdout.write("\x1b[J" + board + "\n")
        sys.stdout.flush()
        prev_lines = board.count("\n") + 1
        try:
            await asyncio.wait_for(done.wait(), timeout=1.0)
        except asyncio.TimeoutError:
            pass
    # final paint
    board = render_board(targets, started)
    if prev_lines:
        sys.stdout.write(f"\x1b[{prev_lines}A")
    sys.stdout.write("\x1b[J" + board + "\n")
    sys.stdout.flush()


# --- final report --------------------------------------------------------------


def yn(v: bool | None) -> str:
    return {True: "ok", False: "FAIL", None: "—"}[v]


def print_report(targets: list[Target], do_chat: bool) -> None:
    print("\n" + "=" * 78)
    print("Results")
    print("=" * 78)
    kw = max(len(t.key) for t in targets)
    header = f"  {'endpoint':<{kw}}  models  chat   latency  detail"
    print(header)
    print("  " + "-" * (len(header) - 2))
    for t in targets:
        lat = f"{t.elapsed:6.0f}s" if t.elapsed else "    — "
        detail = t.error or (t.sample if t.chat_ok else t.served_reported) or ""
        if t.chat_ok and t.finish_reason:
            detail = f"[{t.finish_reason}] {detail}"
        print(f"  {t.key:<{kw}}  {yn(t.models_ok):<6} {yn(t.chat_ok):<5}  {lat}  {detail[:60]}")

    def healthy(t: Target) -> bool:
        return bool(t.models_ok and (t.chat_ok or not do_chat))

    ok = sum(1 for t in targets if healthy(t))
    print("  " + "-" * (len(header) - 2))
    print(f"  {ok}/{len(targets)} healthy" + ("" if do_chat else " (liveness only — chat not tested)"))
    failed = [t.key for t in targets if not healthy(t)]
    if failed:
        print(f"  needs attention: {', '.join(failed)}")


def build_targets(catalogue: ModuleType, workspace: str | None, args) -> list[Target]:
    base_override = os.environ.get("MODAL_LLM_BASE_URL")
    targets: list[Target] = []
    only = {s.strip() for s in args.only.split(",")} if args.only else None
    skip = {s.strip() for s in args.skip.split(",")} if args.skip else set()
    for e in catalogue.entries():
        key = e.key
        if only and key not in only:
            continue
        if key in skip:
            continue
        if args.profiles_only and e.profile not in PROFILE_TIERS:
            continue
        if base_override:
            base_url = base_override.rstrip("/")
        else:
            base_url = catalogue.endpoint_url(e.app, e.endpoint_name, workspace)
        targets.append(
            Target(
                key=key,
                app=e.app,
                served_model_id=e.served_model_id,
                profile=e.profile,
                params_b=e.params_b,
                base_url=base_url,
            )
        )
    return targets


async def main_async(args) -> int:
    catalogue = load_catalogue()
    workspace = resolve_workspace(args.workspace)
    base_override = os.environ.get("MODAL_LLM_BASE_URL")
    if not workspace and not base_override:
        print(
            "ERROR: could not resolve a Modal workspace. Pass --workspace, set "
            "$MODAL_WORKSPACE, or run `modal token new`.",
            file=sys.stderr,
        )
        return 2

    targets = build_targets(catalogue, workspace, args)
    if not targets:
        print("No endpoints matched the given filters.", file=sys.stderr)
        return 2

    api_key = os.environ.get("MODAL_LLM_KEY") or os.environ.get("LLM_API_KEY") or "EMPTY"

    if args.print_urls:
        print(f"workspace: {workspace or '(via MODAL_LLM_BASE_URL override)'}")
        for t in targets:
            tag = f" [{t.profile}]" if t.profile else ""
            print(f"  {t.key:<32}{tag:<12} {t.base_url}")
        return 0

    do_chat = not args.no_chat
    print(
        f"Workspace: {workspace}   endpoints: {len(targets)}   "
        f"chat: {'yes' if do_chat else 'no'}   per-endpoint timeout: {args.timeout}s"
    )
    print("Firing all endpoints concurrently — cold starts overlap, so this takes")
    print("about as long as the single slowest model, not the sum.\n")

    sem = asyncio.Semaphore(args.concurrency if args.concurrency > 0 else len(targets))
    started = time.monotonic()
    done = asyncio.Event()
    progress = asyncio.create_task(progress_loop(targets, started, done))
    try:
        await asyncio.gather(*(run_target(t, api_key, args.timeout, do_chat, sem) for t in targets))
    finally:
        done.set()
        await progress

    print_report(targets, do_chat)

    if args.json:
        summary = [
            {
                "endpoint": t.key,
                "app": t.app,
                "served_model_id": t.served_model_id,
                "base_url": t.base_url,
                "models_ok": t.models_ok,
                "chat_ok": t.chat_ok,
                "latency_s": round(t.elapsed, 1),
                "finish_reason": t.finish_reason,
                "served_reported": t.served_reported,
                "error": t.error,
            }
            for t in targets
        ]
        Path(args.json).write_text(json.dumps(summary, indent=2))
        print(f"\nWrote JSON summary to {args.json}")

    all_ok = all(t.models_ok and (t.chat_ok or not do_chat) for t in targets)
    return 0 if all_ok else 1


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--workspace", help="Modal workspace slug (else $MODAL_WORKSPACE / `modal profile current`)")
    p.add_argument("--only", help="comma-separated endpoint keys to include")
    p.add_argument("--skip", help="comma-separated endpoint keys to exclude")
    p.add_argument(
        "--profiles-only", action="store_true", help="test only the engine-bound tiers (tiny/fast/balanced/strong)"
    )
    p.add_argument("--no-chat", action="store_true", help="liveness only (GET /v1/models); skip the chat completion")
    p.add_argument("--timeout", type=int, default=900, help="per-endpoint deadline in seconds (default 900)")
    p.add_argument("--concurrency", type=int, default=0, help="max endpoints in flight at once (default 0 = all)")
    p.add_argument("--print-urls", action="store_true", help="resolve and print endpoint URLs, then exit (no calls)")
    p.add_argument("--json", help="also write a machine-readable summary to this path")
    return p.parse_args(argv)


def main() -> None:
    args = parse_args()
    try:
        raise SystemExit(asyncio.run(main_async(args)))
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        raise SystemExit(130)


if __name__ == "__main__":
    main()
