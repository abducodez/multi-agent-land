"""Suite-wide test fixtures: the deterministic "mock data" the app no longer ships.

The app has no offline product mode — it requires a real event store
(``DATABASE_URL``) and live inference, and refuses to run without them.  The test
suite must still run with zero network, no credentials, and full reproducibility,
so it supplies that infrastructure as *mock data* instead:

  * an ephemeral in-memory SQLite event store (a real ``SqlAlchemyLedger``, no server);
  * the :class:`~src.models.provider.DeterministicTinyModel` stub for every profile,
    via ``ModelRouter(offline=True)`` — wired in by patching ``Registry.build_router``.

A test that needs the *real* production wiring (e.g. to assert that a missing DB
URL or missing credentials raises) opts out with ``@pytest.mark.real_infra`` and
sets up its own environment.
"""

from __future__ import annotations

import pytest


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "real_infra: use the real make_ledger/build_router wiring instead of the mock fixtures",
    )


@pytest.fixture(autouse=True)
def _mock_infra(request, monkeypatch):
    # An ephemeral SQLite store stands in for the required durable event store.
    monkeypatch.setenv("DATABASE_URL", "sqlite://")

    if request.node.get_closest_marker("real_infra"):
        return

    # Route every profile to the deterministic stub so tests never reach live
    # inference. Patching build_router (rather than env) keeps production code's
    # "live credentials required" contract intact while tests get reproducible data.
    from src.core.registry import Registry
    from src.models.router import ModelRouter, ProfileSpec

    def _stub_build_router(self) -> ModelRouter:
        specs = {profile: ProfileSpec(**cfg.model_dump()) for profile, cfg in self.models.profiles.items()}
        return ModelRouter(offline=True, specs=specs)

    monkeypatch.setattr(Registry, "build_router", _stub_build_router)

    # Media (image / TTS) also defaults to the deterministic stub in tests, mirroring the
    # text router — so a dev with MODAL_WORKSPACE set never makes live media calls in the
    # suite. ``default_tool_registry`` re-imports this name on each call, so the patch sticks.
    from src.media import inference as media_inference
    from src.media.router import MediaRouter

    monkeypatch.setattr(media_inference, "build_media_router", lambda env=None: MediaRouter(offline=True))
