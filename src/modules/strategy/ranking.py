"""Shared ranker-v2 display and instrument-selection rules.

This module contains only pure helpers so strategy refresh, dashboard, fallback,
assistant and paper-trading callers use the same representative ordering.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Mapping
from typing import Any

RANKER_VERSION_V1 = "ranker-v1"
RANKER_VERSION_V2 = "ranker-v2"
SCORE_VERSION_LEGACY = "legacy-score-v1"
SCORE_VERSION_V2 = RANKER_VERSION_V2
RANK_SCORE_CENTER = 100.0
RANK_SCORE_SCALE = 20.0
MISSING_FEATURE_POLICY = "neutral-percentile-50"
TW_CONSTRAINT_POLICY_VERSION = "tw-instrument-constraint-v1"
TW_INSTRUMENT_CAP = 5
TW_HIGH_RISK_SHARE = 0.32
TW_SINGLE_STRATEGY_SHARE = 0.42

ACTION_PRIORITY = {"buy": 3, "add": 3, "hold": 2, "watch": 1, "avoid": 0}


def stable_sigmoid_score(ranking_value: float) -> float:
    """Map an uncapped ranking value to the contract's fixed display tier."""
    value = float(ranking_value)
    if not math.isfinite(value):
        raise ValueError("ranking_value must be finite")
    x = (value - RANK_SCORE_CENTER) / RANK_SCORE_SCALE
    if x >= 0:
        sigmoid = 1.0 / (1.0 + math.exp(-x))
    else:
        exp_x = math.exp(x)
        sigmoid = exp_x / (1.0 + exp_x)
    return 100.0 * sigmoid


def _value(row: Any, name: str, default=None):
    if isinstance(row, Mapping):
        return row.get(name, default)
    return getattr(row, name, default)


def canonical_instrument_id(row: Any) -> str:
    market = str(_value(row, "stock_market", "CN") or "CN").strip().upper()
    instrument = str(_value(row, "instrument_id", "") or "").strip().upper()
    if instrument:
        return instrument
    symbol = str(_value(row, "stock_symbol", "") or "").strip().upper()
    if market == "TW":
        # A Taiwan venue-qualified symbol is already canonical. A bare symbol remains
        # bare so its authority gap is visible to the caller instead of being guessed.
        return symbol
    return f"{market}:{symbol}" if symbol else ""


def _numeric_rank_value(row: Any) -> float:
    value = _value(row, "ranking_value")
    try:
        number = float(value)
        if math.isfinite(number):
            return number
    except (TypeError, ValueError):
        pass
    version = str(_value(row, "ranker_version", "") or _value(row, "score_version", ""))
    if version == RANKER_VERSION_V2:
        return float("-inf")
    has_rank_score = _value(row, "rank_score") is not None
    if version == "candidate-score-v1" or (not has_rank_score and _value(row, "score") is not None):
        try:
            number = float(_value(row, "score"))
            return number if math.isfinite(number) else float("-inf")
        except (TypeError, ValueError):
            return float("-inf")
    # Legacy projections have no uncapped value. Their bounded score preserves their
    # historical ordering while the version travels with the representative.
    if version not in (RANKER_VERSION_V2, SCORE_VERSION_V2):
        try:
            number = float(_value(row, "rank_score", 0.0) or 0.0)
            return number if math.isfinite(number) else float("-inf")
        except (TypeError, ValueError):
            pass
    return float("-inf")


def representative_sort_key(row: Any) -> tuple:
    """Stable representative ordering: active, action, rank value, then identity."""
    market = str(_value(row, "stock_market", "CN") or "CN").strip().upper()
    status = str(_value(row, "status", "inactive") or "inactive").strip().lower()
    action = str(_value(row, "action", "watch") or "watch").strip().lower()
    strategy = str(_value(row, "strategy_code", "") or "").strip().lower()
    if not strategy:
        tags = _value(row, "strategy_tags", []) or []
        strategy = min((str(tag).strip().lower() for tag in tags if str(tag).strip()), default="")
    version = str(_value(row, "ranker_version", "") or _value(row, "score_version", ""))
    source_id = _value(row, "source_candidate_id")
    stable_id = _value(row, "decision_snapshot_id") or (
        f"candidate:{source_id}" if source_id is not None
        else f"signal:{_value(row, 'id', '')}"
    )
    return (
        0 if status == "active" else 1,
        -ACTION_PRIORITY.get(action, 0),
        0 if version in (RANKER_VERSION_V2, SCORE_VERSION_V2) else 1,
        -_numeric_rank_value(row),
        strategy,
        canonical_instrument_id(row),
        stable_id,
        str(_value(row, "id", "")),
        market,
    )


def group_instrument_rows(rows: list[Any]) -> list[tuple[Any, list[Any]]]:
    """Return one deterministic representative and all members per instrument."""
    groups: dict[tuple[str, str], list[Any]] = defaultdict(list)
    for row in rows or []:
        market = str(_value(row, "stock_market", "CN") or "CN").strip().upper()
        instrument_id = canonical_instrument_id(row)
        symbol = str(_value(row, "stock_symbol", "") or "").strip().upper()
        key = (market, instrument_id or f"{market}:missing:{symbol}")
        groups[key].append(row)
    out: list[tuple[Any, list[Any]]] = []
    for key in sorted(groups):
        members = sorted(groups[key], key=representative_sort_key)
        out.append((members[0], members))
    out.sort(key=lambda pair: representative_sort_key(pair[0]))
    return out


def rank_map(values: Mapping[Any, float | None], *, reverse: bool = True) -> dict[Any, float | None]:
    """Average tied rank percentiles; missing and non-finite values stay missing.

    Rank positions use the requested sort direction (position 1 is best). This keeps
    the existing ``reverse=True`` convention where the strongest input receives 100.
    """
    finite: list[tuple[Any, float]] = []
    result: dict[Any, float | None] = {key: None for key in values}
    for key, raw in values.items():
        try:
            number = float(raw)
        except (TypeError, ValueError):
            continue
        if math.isfinite(number):
            finite.append((key, number))
    if not finite:
        return result
    finite.sort(key=lambda item: item[1], reverse=reverse)
    count = len(finite)
    start = 0
    while start < count:
        end = start + 1
        while end < count and finite[end][1] == finite[start][1]:
            end += 1
        if count == 1:
            percentile = 50.0
        else:
            # 1-based inclusive rank positions average to this tied rank.
            average_rank = ((start + 1) + end) / 2.0
            percentile = 100.0 * (count - average_rank) / (count - 1)
        percentile = min(100.0, max(0.0, percentile))
        for index in range(start, end):
            result[finite[index][0]] = percentile
        start = end
    return result
