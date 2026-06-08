"""Guard the env-gating contract for the live/offline wiring (no network, no creds).

These tests pin the *selection* behaviour that hooks the Fishbowl UI to live
services so it cannot silently regress:

  * Models  — :meth:`ModelRouter.from_env` is offline unless a Modal binding is set
    (``MODAL_WORKSPACE`` or ``MODAL_LLM_BASE_URL``), via
    :func:`~src.models.openai_compat.has_live_credentials`.
  * Ledger  — :func:`~src.core.ledger_factory.make_ledger` returns the in-memory
    ``Ledger`` with no ``DATABASE_URL`` and the ``SqlAlchemyLedger`` for a URL.
  * Memory  — :func:`~src.core.memory_index.memory_index_from_env` is ``None`` when
    the gate is unset and a cloud index when ``MEMORY_INDEX=cloud``.

Everything runs on ``monkeypatch`` env edits (auto-reverted), an in-memory SQLite
URL, and optional-dep guards (``pytest.importorskip``) so the suite never touches a
real network, a database server, or live credentials.
"""

from __future__ import annotations

import pytest

from src.core.ledger import Ledger
from src.core.ledger_factory import make_ledger
from src.core.memory_index import memory_index_from_env
from src.models.openai_compat import has_live_credentials
from src.models.router import ModelRouter

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


# ── models: offline by default, live when a Modal binding is present ───────────


class TestModelsOfflineByDefault:
    def test_has_live_credentials_false_when_unset(self):
        assert has_live_credentials() is False

    def test_router_from_env_is_offline_when_unset(self):
        assert ModelRouter.from_env().offline is True


class TestModelsLiveWhenBound:
    def test_workspace_marks_live_credentials(self, monkeypatch):
        monkeypatch.setenv("MODAL_WORKSPACE", "demo-workspace")
        assert has_live_credentials() is True

    def test_workspace_makes_router_live(self, monkeypatch):
        monkeypatch.setenv("MODAL_WORKSPACE", "demo-workspace")
        assert ModelRouter.from_env().offline is False

    def test_base_url_marks_live_credentials(self, monkeypatch):
        monkeypatch.setenv("MODAL_LLM_BASE_URL", "https://demo.modal.run/v1")
        assert has_live_credentials() is True

    def test_base_url_makes_router_live(self, monkeypatch):
        monkeypatch.setenv("MODAL_LLM_BASE_URL", "https://demo.modal.run/v1")
        assert ModelRouter.from_env().offline is False

    def test_blank_workspace_stays_offline(self, monkeypatch):
        # An empty/whitespace value must NOT count as a live binding.
        monkeypatch.setenv("MODAL_WORKSPACE", "   ")
        assert has_live_credentials() is False
        assert ModelRouter.from_env().offline is True


# ── ledger: in-memory by default, SqlAlchemy-backed for a URL ──────────────────


class TestLedgerSelection:
    def test_no_url_returns_in_memory_ledger(self):
        ledger = make_ledger()
        assert type(ledger) is Ledger

    def test_sqlite_url_returns_sqlalchemy_ledger(self):
        pytest.importorskip("sqlalchemy")
        from src.core.sqlalchemy_ledger import SqlAlchemyLedger

        ledger = make_ledger("sqlite:///:memory:")
        assert isinstance(ledger, SqlAlchemyLedger)

    def test_sqlite_ledger_append_read_roundtrip(self):
        pytest.importorskip("sqlalchemy")
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
