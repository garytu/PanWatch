from datetime import UTC, datetime

from pan_agent import (
    AgentCheckpoint,
    ModelMessage,
    PendingApproval,
    RunLimits,
    ToolResult,
    ToolRisk,
    ToolSpec,
)


def test_tool_schema_and_default_runtime_limits_are_stable():
    spec = ToolSpec(
        name="lookup",
        title="查詢",
        description="Read a value",
        risk=ToolRisk.READ,
        input_schema={"type": "object", "properties": {"key": {"type": "string"}}},
    )

    assert spec.openai_schema()["function"]["name"] == "lookup"
    assert RunLimits().model_dump() == {
        "max_steps": 6,
        "max_tool_calls": 8,
        "tool_timeout_seconds": 20,
        "tool_timeout_overrides": {},
        "run_timeout_seconds": 90,
        "step_retry_count": 1,
    }


def test_per_tool_timeout_override_is_bounded_and_scoped():
    limits = RunLimits(tool_timeout_seconds=15, tool_timeout_overrides={"research": 28})
    assert limits.tool_timeout_overrides == {"research": 28}

    for invalid in ({"research": 0}, {"research": 121}, {"Bad Name": 20}):
        try:
            RunLimits(tool_timeout_overrides=invalid)
        except ValueError:
            pass
        else:
            raise AssertionError(f"invalid override accepted: {invalid}")


def test_successful_tool_results_require_freshness_and_provenance():
    observed_at = datetime.now(UTC)
    result = ToolResult.success(
        summary="查詢完成",
        data={"price": 1},
        sources=[{"name": "行情源", "url": "https://example.test/quote"}],
        observed_at=observed_at,
    )

    assert result.ok is True
    assert result.observed_at == observed_at
    assert result.sources[0].name == "行情源"


def test_pending_approval_round_trips_through_a_json_checkpoint():
    checkpoint = AgentCheckpoint(
        messages=[ModelMessage(role="user", content="hello")],
        step_index=2,
        tool_calls_used=1,
        pending_approvals=[
            PendingApproval(
                call_id="call-1",
                tool_name="create_alert",
                risk=ToolRisk.WRITE,
                arguments={"symbol": "CN:601238"},
            )
        ],
    )

    restored = AgentCheckpoint.model_validate(checkpoint.model_dump(mode="json"))

    assert restored.pending_approvals[0].call_id == "call-1"
    assert restored.pending_approvals[0].arguments == {"symbol": "CN:601238"}


def test_timeout_overrides_reject_boolean_and_fractional_values():
    import pytest
    for value in (True, 1.5):
        with pytest.raises(ValueError):
            RunLimits(tool_timeout_overrides={"research": value})
