"""MCP transport tests (ADR-0017).

Three tiers, mirroring the optional-dependency tests elsewhere:

  * Capability enforcement wraps the MCP transport — provable with no ``mcp``
    installed and no server, via a spy resolver: a denied grant raises
    ``CapabilityViolation`` *before* the resolver is ever consulted, and the gate
    flips the registry in-process ↔ MCP from the environment.
  * The server module imports and registers ``oracle`` (guarded with
    ``importorskip``).
  * A guarded stdio round-trip asserts ``oracle`` returns an omen over MCP
    (skipped if ``mcp`` is absent or the server can't be spawned quickly).
"""

from __future__ import annotations

import pytest

from src.core.registry import default_registry
from src.tools.builtins import default_tool_registry
from src.tools.registry import CapabilityViolation, ToolRegistry


class _SpyResolver:
    """A ToolResolver that records whether it was consulted (no real transport)."""

    def __init__(self, tools: dict[str, str]) -> None:
        self._tools = tools
        self.calls: list[tuple[str, dict]] = []
        self.has_checks: list[str] = []

    def has(self, tool: str) -> bool:
        self.has_checks.append(tool)
        return tool in self._tools

    def describe(self, tool: str) -> str:
        return self._tools.get(tool, "")

    def call(self, tool: str, params: dict) -> dict:
        self.calls.append((tool, params))
        return {"omen": f"spy:{params.get('seed', '')}"}


# ── capability enforcement wraps the transport (no mcp required) ────────────────


class TestCapabilityWrapsTransport:
    def test_denied_grant_raises_before_resolver_is_touched(self):
        """The grant check fires before MCP dispatch, regardless of transport."""
        reg = default_registry()
        no_grant = reg.agents["scene-whisperer"]  # tools: []
        resolver = _SpyResolver({"oracle": "omen over MCP"})
        tools = ToolRegistry()
        tools.set_resolver(resolver)

        with pytest.raises(CapabilityViolation):
            tools.call("scene-whisperer", no_grant, "oracle", {"seed": "x"})

        # The security boundary held *before* any transport work happened.
        assert resolver.calls == []
        assert resolver.has_checks == []

    def test_granted_call_dispatches_over_resolver(self):
        reg = default_registry()
        granted = reg.agents["fortune-teller"]  # tools: [oracle]
        resolver = _SpyResolver({"oracle": "omen over MCP"})
        tools = ToolRegistry()
        tools.set_resolver(resolver)

        result = tools.call("fortune-teller", granted, "oracle", {"seed": "grove"})

        assert result == {"omen": "spy:grove"}
        assert resolver.calls == [("oracle", {"seed": "grove"})]

    def test_describe_uses_resolver_when_not_in_process(self):
        resolver = _SpyResolver({"oracle": "omen over MCP"})
        tools = ToolRegistry()
        tools.set_resolver(resolver)
        assert "omen over MCP" in tools.describe(["oracle"])
        assert tools.describe(["nope"]) == ""  # unknown still skipped

    def test_in_process_takes_precedence_over_resolver(self):
        """A locally registered tool is served in-process even if a resolver exists."""
        reg = default_registry()
        granted = reg.agents["fortune-teller"]
        resolver = _SpyResolver({"oracle": "omen over MCP"})
        tools = default_tool_registry()  # registers oracle in-process (gate unset)
        tools.set_resolver(resolver)

        result = tools.call("fortune-teller", granted, "oracle", {"seed": "x"})
        assert "omen" in result
        assert resolver.calls == []  # resolver never reached

    def test_granted_but_unresolved_tool_raises_keyerror(self):
        reg = default_registry()
        granted = reg.agents["fortune-teller"]
        tools = ToolRegistry()  # no in-process tool, no resolver
        with pytest.raises(KeyError):
            tools.call("fortune-teller", granted, "oracle", {"seed": "x"})


# ── the config gate flips in-process ↔ MCP (no mcp required) ────────────────────


class TestConfigGate:
    def test_default_is_in_process(self, monkeypatch):
        monkeypatch.delenv("MCP_SERVERS", raising=False)
        monkeypatch.delenv("MCP_ORACLE", raising=False)
        tools = default_tool_registry()
        assert tools.has("oracle")  # in-process registration present
        assert "oracle" in tools.describe(["oracle"])

    def test_server_configs_from_env_unset(self):
        from src.tools.mcp_client import server_configs_from_env

        assert server_configs_from_env({}) == []

    def test_mcp_oracle_gate_selects_default_server(self):
        from src.tools.mcp_client import server_configs_from_env

        configs = server_configs_from_env({"MCP_ORACLE": "1"})
        assert len(configs) == 1
        assert configs[0].command == "python"
        assert configs[0].args == ("-m", "src.tools.mcp_server")

    def test_mcp_servers_gate_parses_multiple(self):
        from src.tools.mcp_client import server_configs_from_env

        configs = server_configs_from_env({"MCP_SERVERS": "python -m src.tools.mcp_server :: node other.js --flag"})
        assert len(configs) == 2
        assert configs[0].command == "python"
        assert configs[1].command == "node"
        assert configs[1].args == ("other.js", "--flag")

    def test_mcp_servers_takes_precedence_over_oracle_flag(self):
        from src.tools.mcp_client import server_configs_from_env

        configs = server_configs_from_env({"MCP_SERVERS": "python -m custom.server", "MCP_ORACLE": "1"})
        assert len(configs) == 1
        assert configs[0].args == ("-m", "custom.server")

    def test_resolver_from_env_none_when_unset(self):
        from src.tools.mcp_client import mcp_resolver_from_env

        assert mcp_resolver_from_env({}) is None


# ── result coercion (no mcp required: pure dataclass shaping) ────────────────────


class TestResultCoercion:
    def test_prefers_structured_content(self):
        from src.tools.mcp_client import _result_to_dict

        class _R:
            isError = False
            structuredContent = {"omen": "structured"}
            content: list = []

        assert _result_to_dict("oracle", _R()) == {"omen": "structured"}

    def test_falls_back_to_json_text(self):
        from src.tools.mcp_client import _result_to_dict

        class _Block:
            text = '{"omen": "from text"}'

        class _R:
            isError = False
            structuredContent = None
            content = [_Block()]

        assert _result_to_dict("oracle", _R()) == {"omen": "from text"}

    def test_error_result_raises(self):
        from src.tools.mcp_client import _result_to_dict

        class _Block:
            text = "boom"

        class _R:
            isError = True
            structuredContent = None
            content = [_Block()]

        with pytest.raises(RuntimeError):
            _result_to_dict("oracle", _R())


# ── server module registers oracle (requires mcp) ───────────────────────────────


class TestMCPServer:
    def test_server_builds_and_registers_oracle(self):
        pytest.importorskip("mcp")
        import anyio

        from src.tools.mcp_server import build_server

        server = build_server()
        tools = anyio.run(server.list_tools)
        names = {t.name for t in tools}
        assert "oracle" in names

    def test_server_oracle_returns_omen(self):
        pytest.importorskip("mcp")
        import anyio

        from src.tools.mcp_server import build_server

        server = build_server()
        result = anyio.run(server.call_tool, "oracle", {"seed": "the glass forest"})
        # FastMCP returns (content, structured) for a typed tool; assert the omen.
        structured = result[1] if isinstance(result, tuple) else {}
        assert "omen" in structured


# ── guarded stdio round-trip (requires mcp + a spawnable server) ─────────────────


class TestMCPStdioRoundTrip:
    def test_oracle_over_stdio(self):
        pytest.importorskip("mcp")
        from src.tools.builtins import oracle
        from src.tools.mcp_client import MCPServerConfig, MCPToolClient

        client = MCPToolClient(server=MCPServerConfig(command="python", args=("-m", "src.tools.mcp_server")))
        try:
            listed = client.list_tools()
        except Exception as exc:  # pragma: no cover - environment dependent
            pytest.skip(f"could not spawn MCP server: {exc}")

        assert "oracle" in listed
        result = client.call("oracle", {"seed": "the glass forest"})
        assert "omen" in result
        # Same deterministic implementation in-process and over MCP.
        assert result == oracle(seed="the glass forest")
