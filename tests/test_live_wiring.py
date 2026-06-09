"""Guard the env-gating contract for the live wiring (no network, no creds).

The app has no offline product mode: it requires live inference and a real event
store and refuses to run without them.  These tests pin that *selection* behaviour
so it cannot silently regress:

  * Models  — :func:`~src.models.openai_compat.has_live_credentials` is False unless a
    Modal/HF binding is set, and :meth:`ModelRouter.from_env` always builds the live
    path (the deterministic stub is reachable only via ``ModelRouter(offline=True)``).
  * Ledger  — :func:`~src.core.ledger_factory.make_ledger` raises with no ``DATABASE_URL``
    and returns the ``SqlAlchemyLedger`` for a URL.
  * Memory  — :func:`~src.core.memory_index.memory_index_from_env` is ``None`` when
    the gate is unset and a cloud index when ``MEMORY_INDEX=cloud``.

Everything runs on ``monkeypatch`` env edits (auto-reverted) and an in-memory SQLite
URL so the suite never touches a real network, a database server, or live credentials.
These cases assert the real wiring, so they opt out of the mock-infra fixture.
"""

from __future__ import annotations

import pytest

from src.core.ledger_factory import make_ledger
from src.core.memory_index import memory_index_from_env
from src.models.openai_compat import has_live_credentials
from src.models.provider import DeterministicTinyModel
from src.models.router import ModelRouter

pytestmark = pytest.mark.real_infra

# Env vars these tests touch; cleared before each case so the host environment and
# sibling tests never leak into (or out of) a case.
_LIVE_ENV = (
    "MODAL_WORKSPACE",
    "MODAL_LLM_BASE_URL",
    "DATABASE_URL",
    "MEMORY_INDEX",
    "MEMORY_INDEX_BACKEND",
    "MEM0_API_KEY",
)


@pytest.fixture(autouse=True)
def _clean_live_env(monkeypatch):
    """Start each case from a known-clean slate (monkeypatch auto-reverts after)."""
    for name in _LIVE_ENV:
        monkeypatch.delenv(name, raising=False)


# ── models: always live; the stub is reachable only by explicit opt-in ─────────


class TestCredentialDetection:
    def test_has_live_credentials_false_when_unset(self):
        assert has_live_credentials() is False

    def test_workspace_marks_live_credentials(self, monkeypatch):
        monkeypatch.setenv("MODAL_WORKSPACE", "demo-workspace")
        assert has_live_credentials() is True

    def test_base_url_marks_live_credentials(self, monkeypatch):
        monkeypatch.setenv("MODAL_LLM_BASE_URL", "https://demo.modal.run/v1")
        assert has_live_credentials() is True

    def test_blank_workspace_is_not_a_binding(self, monkeypatch):
        # An empty/whitespace value must NOT count as a live binding.
        monkeypatch.setenv("MODAL_WORKSPACE", "   ")
        assert has_live_credentials() is False


class TestRouterAlwaysLive:
    def test_from_env_is_live_when_unset(self):
        # No offline auto-detection: from_env always builds the live path.
        router = ModelRouter.from_env()
        assert router.offline is False
        assert not isinstance(router.for_profile("fast"), DeterministicTinyModel)

    def test_from_env_is_live_when_bound(self, monkeypatch):
        monkeypatch.setenv("MODAL_WORKSPACE", "demo-workspace")
        assert ModelRouter.from_env().offline is False

    def test_stub_only_via_explicit_offline(self):
        # The deterministic stub is the test seam, never selected automatically.
        assert isinstance(ModelRouter(offline=True).for_profile("fast"), DeterministicTinyModel)


# ── ledger: required — raises with no URL, SqlAlchemy-backed for a URL ──────────


class TestLedgerSelection:
    def test_no_url_raises(self):
        with pytest.raises(RuntimeError, match="DATABASE_URL is required"):
            make_ledger()

    def test_sqlite_url_returns_sqlalchemy_ledger(self):
        from src.core.sqlalchemy_ledger import SqlAlchemyLedger

        ledger = make_ledger("sqlite:///:memory:")
        assert isinstance(ledger, SqlAlchemyLedger)

    def test_sqlite_ledger_append_read_roundtrip(self):
        from src.core.events import Event

        ledger = make_ledger("sqlite:///:memory:")
        event = Event(run_id="r1", turn=0, kind="run.started", actor="engine", payload={"text": "hi"})
        ledger.append(event)
        assert [e.id for e in ledger.events] == [event.id]


# ── memory: None when the gate is unset, cloud index when MEMORY_INDEX=cloud ───


class TestMemoryIndexSelection:
    def test_none_when_gate_unset(self):
        assert memory_index_from_env() is None

    def test_cloud_index_when_gate_is_cloud(self, monkeypatch):
        pytest.importorskip("mem0")
        from src.core.memory_index import Mem0CloudIndex

        monkeypatch.setenv("MEMORY_INDEX", "cloud")
        monkeypatch.setenv("MEM0_API_KEY", "dummy-key")
        index = memory_index_from_env()
        assert isinstance(index, Mem0CloudIndex)
