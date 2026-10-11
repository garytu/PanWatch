"""Host-owned execution bounds for tools with known provider latency."""

# Profile snapshots can take up to 20 seconds. The Taiwan research aggregator
# has a 25-second wall deadline and returns each completed block independently.
ASSISTANT_TOOL_TIMEOUT_OVERRIDES = {
    "get_taiwan_stock_research": 28,
    "get_stock_fundamentals": 28,
    "get_capital_flow": 28,
}
