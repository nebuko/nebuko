"""Tests for the engine-tool MCP server and the finalize sink (issue #1368)."""

from __future__ import annotations

import asyncio

import pytest

from nebuko.engine.harness.engine_tools import (
    MCP_SERVER_NAME,
    TOOL_PREFIX,
    EngineToolServer,
    FinalizeSink,
)


def _sink() -> FinalizeSink:
    return FinalizeSink()


def _server(sink: FinalizeSink) -> EngineToolServer:
    return EngineToolServer(
        role_name="dev", agent_cfg=None, agent_context=None, sink=sink
    )


def test_finalize_sink_round_trips_the_report_and_outcome():
    sink = _sink()
    sink.record(
        {"report": "## Report\n\n- done", "turn_outcome": {"appreciation": "approved"}}
    )
    result = sink.result()
    assert result is not None
    assert result.report == "## Report\n\n- done"
    assert result.outcome is not None
    assert result.outcome.appreciation == "approved"


def test_finalize_sink_rejects_a_hold_without_hold_kind():
    sink = _sink()
    with pytest.raises(ValueError, match="hold_kind"):
        sink.record({"report": "r", "turn_outcome": {"appreciation": "hold"}})


def test_finalize_sink_reset_clears_the_result():
    sink = _sink()
    sink.record({"report": "r", "turn_outcome": {"appreciation": "approved"}})
    sink.reset()
    assert sink.result() is None


def test_engine_tool_server_exposes_finalize_with_schema():
    sink = _sink()
    handlers = _server(sink).handlers()
    assert len(handlers) == 5
    result = asyncio.run(
        handlers[0]({"report": "r", "turn_outcome": {"appreciation": "rejected"}})
    )
    assert result["content"][0]["text"].startswith("Turn outcome recorded")
    assert sink.result().outcome.appreciation == "rejected"


def test_engine_tool_server_finalize_error_is_returned_to_the_agent():
    sink = _sink()
    handler = _server(sink).handlers()[0]
    result = asyncio.run(handler({"report": "r", "turn_outcome": {}}))
    assert result["is_error"] is True
    assert "finalize rejected" in result["content"][0]["text"]


def test_engine_tool_server_as_sdk_server_returns_server():
    pytest.importorskip("claude_agent_sdk")
    server = _server(_sink()).as_sdk_server()
    assert server["type"] == "sdk"
    assert server["name"] == MCP_SERVER_NAME
    assert TOOL_PREFIX == "mcp__nebuko__"
