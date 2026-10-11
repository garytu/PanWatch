"""Replay a deterministic synthetic ranker-v2 and TW selection cohort offline.

This fixture exercises the scoring display, filters, sizing bands, representatives,
instrument cap and quota metadata. It does not read a database or modify calibration.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
import sys
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.modules.strategy.ranking import rank_map, stable_sigmoid_score
from src.modules.strategy.strategy_engine import _tw_instrument_selection
from src.modules.paper_trading.paper_trading_engine import _position_weight


def replay() -> dict:
    raw_values = [50, 60, 70, 80, 90, 100, 110, 110, 120, 130, 140, 150, 160]
    percentile = rank_map({index: value for index, value in enumerate(raw_values)})
    candidates = []
    for index, raw_value in enumerate(raw_values):
        strategy = f"strategy-{index % 5}"
        candidates.append(SimpleNamespace(
            id=index + 1,
            source_candidate_id=index + 1,
            stock_market="TW",
            stock_symbol=f"TWSE:{2300 + index}",
            instrument_id=f"TWSE:{2300 + index}",
            instrument_group="EQUITY",
            strategy_code=strategy,
            status="active",
            action="buy",
            ranker_version="ranker-v2",
            score_version="ranker-v2",
            ranking_value=float(raw_value),
            rank_score=stable_sigmoid_score(raw_value),
            risk_level="high" if raw_value == 160 else "medium",
            is_holding_snapshot=False,
            selected_for_portfolio=False,
        ))

    representatives = [(row, [row]) for row in candidates]
    selection = _tw_instrument_selection(
        representatives, cap=5, risk_share=0.32, strategy_share=0.42,
    )
    selected = sorted(selection["selected"], key=lambda pair: -pair[0].ranking_value)
    thresholds = (55, 70, 80, 90)
    filter_retention = {}
    for threshold in thresholds:
        legacy_count = sum(min(row.ranking_value, 100.0) >= threshold for row in candidates)
        v2_count = sum(row.rank_score >= threshold for row in candidates)
        filter_retention[str(threshold)] = {
            "legacy_count": legacy_count,
            "ranker_v2_count": v2_count,
            "delta": v2_count - legacy_count,
        }

    v2_exposure = sum(_position_weight(float(row.rank_score)) for row, _ in selected)
    legacy_exposure = sum(
        _position_weight(min(float(row.ranking_value), 100.0)) for row, _ in selected
    )
    display_scores = [round(float(row.rank_score), 8) for row in candidates]
    display_ties = sum(1 for count in Counter(display_scores).values() if count > 1)
    sizing_examples = []
    for value in (110, 130, 150):
        score = stable_sigmoid_score(value)
        sizing_examples.append({
            "ranking_value": value,
            "rank_score": round(score, 8),
            "v2_budget_ratio": _position_weight(score),
            "legacy_budget_ratio_after_cap": _position_weight(min(value, 100.0)),
        })

    return {
        "fixture": "rc-c-frozen-synthetic-v1",
        "calibration_mode": "FROZEN",
        "calibration_writes": False,
        "source_data": "deterministic synthetic inputs only; no live or historical rows",
        "reference": {
            "candidate_count": len(candidates),
            "finite_percentile_count": sum(value is not None for value in percentile.values()),
            "feature_tie_groups": sum(
                1 for count in Counter(raw_values).values() if count > 1
            ),
            "display_tie_groups": display_ties,
            "ranking_value_min": min(raw_values),
            "ranking_value_max": max(raw_values),
            "rank_score_min": round(min(display_scores), 8),
            "rank_score_median": round(sorted(display_scores)[len(display_scores) // 2], 8),
            "rank_score_max": round(max(display_scores), 8),
        },
        "filter_retention": filter_retention,
        "sizing_examples": sizing_examples,
        "selection": {
            "constraint_unit": "INSTRUMENT",
            "cap": 5,
            "before_instrument_count": selection["before_instrument_count"],
            "after_instrument_count": selection["after_instrument_count"],
            "selected_instruments": [row.instrument_id for row, _ in selected],
            "excluded_instrument_count": len(selection["excluded"]),
            "exclusion_reasons": {
                code: sum(code in reasons for reasons in selection["excluded"].values())
                for code in ("UNHELD_CAP", "HIGH_RISK_RATIO", "STRATEGY_SHARE")
            },
            "high_risk_quota": selection["risk_quota"],
            "high_risk_actual_ratio": selection["risk_actual_ratio"],
            "strategy_quotas": selection["strategy_quotas"],
            "small_set_quota_exceptions": selection["small_set_quota_exceptions"],
        },
        "exposure": {
            "measure": "sum of requested per-position target budget ratios; not executed allocation",
            "cash_or_lot_simulation": False,
            "comparison": "same v2 selected instruments; legacy capped display only, not a historical portfolio",
            "selected_position_budget_ratio_v2": round(v2_exposure, 8),
            "selected_position_budget_ratio_legacy_capped": round(legacy_exposure, 8),
            "delta": round(v2_exposure - legacy_exposure, 8),
        },
    }


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = replay()
    output = json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    if args.output:
        args.output.write_text(output, encoding="utf-8")
    else:
        print(output, end="")


if __name__ == "__main__":
    main()
