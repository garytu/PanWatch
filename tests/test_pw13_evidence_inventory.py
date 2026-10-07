import json
import subprocess
import sys
from pathlib import Path

from scripts.research.pw13_historical_research import build_checked_evidence_dataset


ROOT = Path(__file__).resolve().parents[1]


def test_checked_evidence_inventory_is_bounded_reproducible_and_insufficient():
    dataset, inventory = build_checked_evidence_dataset(ROOT)

    assert inventory["fixed_instrument_scope"] == [
        "TPEX:006201", "TPEX:5347", "TWSE:00878", "TWSE:2330",
    ]
    assert inventory["evaluation"]["status"] == "insufficient_evidence"
    assert inventory["evaluation"]["evaluation_mode"] == "inventory_only"
    assert inventory["evaluation"]["evaluation_performed"] is False
    assert inventory["evaluation"]["counts"]["input_signals"] == 12
    assert inventory["evaluation"]["counts"]["availability_audit_rows"] == 12
    assert inventory["evaluation"]["counts"]["evaluation_samples"] == 0
    assert inventory["evaluation"]["efficacy_claim_allowed"] is False
    assert inventory["evaluation"]["dataset_sha256"] == inventory["input_sha256"]
    assert len(inventory["retained_factor_observations"]) == 20

    audit_by_key = {
        (row["decision_date_taipei"], row["instrument_id"]): row
        for row in inventory["evaluation"]["availability_audit"]
    }
    oct_2_twse = audit_by_key[("2026-10-02", "TWSE:2330")]["conditions"]
    assert oct_2_twse["pe_max"]["reason"] == "future_revision_or_receipt"
    assert oct_2_twse["pe_max"]["data_period"] == "2026-10-02"
    assert oct_2_twse["pe_max"]["provenance"]["available_at_utc"] == "2026-10-06T15:57:48Z"
    oct_2_tpex = audit_by_key[("2026-10-02", "TPEX:5347")]["conditions"]
    assert oct_2_tpex["pe_max"]["data_period"] == "2026-10-02"
    assert oct_2_tpex["pe_max"]["provenance"]["available_at_utc"] == "2026-10-04T13:09:33.499001Z"

    oct_7_twse = audit_by_key[("2026-10-07", "TWSE:2330")]["conditions"]
    assert oct_7_twse["pe_max"]["status"] == "available_by_decision"
    assert oct_7_twse["pe_max"]["data_period"] == "2026-10-05"
    assert oct_7_twse["pe_max"]["provenance"]["snapshot_sha256"]
    assert oct_7_twse["revenue_yoy_min_pct"]["value"] == "53.320053714712955"
    assert oct_7_twse["institutional_net_min_shares"]["reason"] == "latest_admissible_value_missing"

    oct_7_tpex = audit_by_key[("2026-10-07", "TPEX:5347")]["conditions"]
    assert oct_7_tpex["pe_max"]["reason"] == "latest_admissible_value_missing"
    assert oct_7_tpex["pe_max"]["data_period"] == "2026-10-05"
    assert oct_7_tpex["revenue_yoy_min_pct"]["value"] == "40.47646040957919"

    tpex_equity = next(row for row in inventory["monthly_revenue_windows"] if row["instrument_id"] == "TPEX:5347")
    assert [(row["data_month"], row["presence"]) for row in tpex_equity["months"]] == [
        ("2026-07", "missing"), ("2026-08", "present"),
    ]

    taiwan_equity = next(row for row in inventory["raw_price_and_venue_benchmark"] if row["instrument_id"] == "TWSE:2330")
    tpex_equity_benchmark = next(row for row in inventory["raw_price_and_venue_benchmark"] if row["instrument_id"] == "TPEX:5347")
    assert taiwan_equity["stock_series"]["requested_period_returned_count"] == 24
    assert taiwan_equity["venue"] == "TWSE"
    assert tpex_equity_benchmark["venue"] == "TPEX"
    assert taiwan_equity["benchmark_series"]["returned_count"] == 24
    assert taiwan_equity["comparison_observation_count"] == 24
    assert tpex_equity_benchmark["stock_series"]["requested_period_returned_count"] == 23
    assert tpex_equity_benchmark["benchmark_series"]["returned_count"] == 4
    assert tpex_equity_benchmark["comparison_observation_count"] == 4

    financial = inventory["financial_statement_revision"]
    assert financial["fiscal_year"] == 2024
    assert financial["document_first_observed_at_utc"] == financial["semantic_revision_first_observed_at_utc"]
    assert financial["point_in_time_eligible_for_2024_decisions"] is False
    assert financial["published_at_utc"] is None

    dataset_again, inventory_again = build_checked_evidence_dataset(ROOT)
    assert dataset_again == dataset
    assert inventory_again["input_sha256"] == inventory["input_sha256"]
    assert inventory_again["evaluation"]["dataset_sha256"] == inventory["evaluation"]["dataset_sha256"]
    assert inventory_again["evaluation"]["report_sha256"] == inventory["evaluation"]["report_sha256"]

    retained = json.loads((ROOT / "docs/plans/twmd-integration/evidence/PW-13-offline-inventory-2026-10-08.json").read_text())
    assert retained == inventory


def test_cli_retains_snapshot_and_refuses_overwriting_a_different_version(tmp_path):
    snapshot = tmp_path / "snapshot.json"
    report = tmp_path / "report.json"
    cli = [sys.executable, str(ROOT / "scripts/research/pw13_historical_research.py")]
    result = subprocess.run(cli + ["--export-snapshot", str(snapshot), "--output", str(report)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    original = snapshot.read_bytes()
    dataset = json.loads(original)
    inventory = json.loads(report.read_text())
    assert inventory["evaluation"]["dataset_sha256"] == build_checked_evidence_dataset(ROOT)[1]["input_sha256"]
    replay = subprocess.run(cli + ["--input", str(snapshot), "--export-snapshot", str(snapshot), "--output", str(report)], capture_output=True, text=True)
    assert replay.returncode == 0, replay.stderr
    assert json.loads(report.read_text()) == inventory["evaluation"]
    different = tmp_path / "different.json"
    dataset["dataset_id"] = "new-version"
    different.write_text(json.dumps(dataset))
    rejected = subprocess.run(cli + ["--input", str(different), "--export-snapshot", str(snapshot)], capture_output=True, text=True)
    assert rejected.returncode != 0
    assert "different content" in rejected.stderr
    assert snapshot.read_bytes() == original
    alias = subprocess.run(cli + ["--input", str(snapshot), "--output", str(snapshot)], capture_output=True, text=True)
    assert alias.returncode != 0
    assert "different path" in alias.stderr
    assert snapshot.read_bytes() == original
