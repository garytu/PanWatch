from __future__ import annotations

import copy
import json
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from marketdata.errors import TwmdReadError
from marketdata.vendors.twmd import TwmdClient


FIXTURE = Path(__file__).parent / "fixtures/twmd/synthetic/corporate_actions.json"


def _fixture():
    raw = json.loads(FIXTURE.read_text(encoding="utf-8"))
    return {
        key: [
            {field: Decimal(value) if isinstance(value, str) and field not in {"effective_date", "recovery_date", "instrument_id", "symbol", "observed_name", "action_kind", "reduction_reason", "provider", "currency"} and value != "" else value for field, value in row.items()}
            for row in raw[key]
        ]
        for key in ("ex_right_dividend_results", "capital_reduction_results")
    }


class FixtureClient(TwmdClient):
    def __init__(self, data):
        super().__init__({"base_url": "http://offline-corporate-actions"})
        self.data = data
        self.calls = []

    def get_response(self, path, **params):
        self.calls.append((path, params))
        return copy.deepcopy(self.data[path.replace("-", "_")]), {}


def test_reads_preserve_decimal_tokens_distinct_same_day_kinds_and_letter_codes():
    client = FixtureClient(_fixture())
    ex = client.ex_right_dividend_results(
        "TWSE:123A", "2024-06-25", "2024-06-25", today_taipei=date(2024, 7, 2)
    )
    reduction = client.capital_reduction_results(
        "TWSE:123A", "2024-07-01", "2024-07-01", today_taipei=date(2024, 7, 2)
    )

    assert [row.action_kind for row in ex.data] == ["ex_right", "ex_dividend"]
    assert ex.data[0].prior_close == "100.00"
    assert ex.data[0].reference_price == "98.765"
    assert ex.data[0].rights_dividend_value == "1.235"
    assert reduction.data[0].recovery_reference_price == "62.5000"
    assert reduction.data[0].ex_right_reference_price is None
    assert ex.dataset_coverage == reduction.dataset_coverage == "unknown"
    assert [(path, args["parse"], args["retries"]) for path, args in client.calls] == [
        ("ex-right-dividend-results", "json_decimal", 0),
        ("capital-reduction-results", "json_decimal", 0),
    ]


@pytest.mark.parametrize(
    ("method", "start", "end"),
    [
        ("ex_right_dividend_results", "2003-05-04", "2003-05-04"),
        ("capital_reduction_results", "2010-12-31", "2010-12-31"),
        ("ex_right_dividend_results", "2024-01-01", "2025-01-01"),
        ("ex_right_dividend_results", "2024-01-02", "2024-01-01"),
        ("ex_right_dividend_results", "2024-06-25", "2024-06-25"),
    ],
)
def test_reads_validate_product_and_completed_date_bounds(method, start, end):
    client = FixtureClient(_fixture())
    if start == "2024-06-25":
        symbol, today = "TPEX:123A", date(2024, 7, 2)
    elif end == "2025-01-01":
        symbol, today = "TWSE:123A", date(2025, 1, 2)
    else:
        symbol, today = "TWSE:123A", date(2024, 7, 2)
    with pytest.raises(ValueError):
        getattr(client, method)(symbol, start, end, today_taipei=today)
    assert not client.calls


def test_exactly_366_calendar_days_are_allowed():
    client = FixtureClient({"ex_right_dividend_results": [], "capital_reduction_results": []})
    result = client.ex_right_dividend_results(
        "TWSE:123A", "2024-01-01", "2024-12-31", today_taipei=date(2025, 1, 1)
    )
    assert result.status == "unknown"


def test_empty_list_is_unknown_and_bad_duplicate_or_identity_fails_closed():
    client = FixtureClient({"ex_right_dividend_results": [], "capital_reduction_results": []})
    empty = client.ex_right_dividend_results(
        "TWSE:123A", "2024-06-25", "2024-06-25", today_taipei=date(2024, 7, 2)
    )
    assert empty.status == "unknown"
    assert empty.reason == "coverage_not_returned"

    rows = _fixture()
    rows["ex_right_dividend_results"].append(copy.deepcopy(rows["ex_right_dividend_results"][0]))
    duplicate = FixtureClient(rows)
    with pytest.raises(TwmdReadError) as exc:
        duplicate.ex_right_dividend_results(
            "TWSE:123A", "2024-06-25", "2024-06-25", today_taipei=date(2024, 7, 2)
        )
    assert exc.value.reason_code == "invalid_response"

    rows = _fixture()
    rows["ex_right_dividend_results"][0]["instrument_id"] = "TPEX:123A"
    wrong_identity = FixtureClient(rows)
    with pytest.raises(TwmdReadError):
        wrong_identity.ex_right_dividend_results(
            "TWSE:123A", "2024-06-25", "2024-06-25", today_taipei=date(2024, 7, 2)
        )


@pytest.mark.parametrize("method,field,value", [
    ("ex_right_dividend_results", "prior_close", "-1"),
    ("ex_right_dividend_results", "reference_price", "-1"),
    ("ex_right_dividend_results", "limit_up_price", "NaN"),
    ("capital_reduction_results", "pre_suspension_close", "0"),
    ("capital_reduction_results", "recovery_reference_price", "-1"),
    ("capital_reduction_results", "ex_right_reference_price", "-1"),
])
def test_source_price_constraints_reject_invalid_values(method, field, value):
    data = _fixture()
    data[method][0][field] = value
    client = FixtureClient(data)
    day = "2024-06-25" if method.startswith("ex_right") else "2024-07-01"
    with pytest.raises(TwmdReadError):
        getattr(client, method)("TWSE:123A", day, day, today_taipei=date(2024, 7, 2))


def test_signed_combined_adjustment_and_large_exact_tokens_remain_source_values():
    data = _fixture()
    data["ex_right_dividend_results"][0]["rights_dividend_value"] = "-1.235"
    token = "123456789012345678901234567890.001234567890123456789"
    data["ex_right_dividend_results"][0]["prior_close"] = token
    read = FixtureClient(data).ex_right_dividend_results(
        "TWSE:123A", "2024-06-25", "2024-06-25", today_taipei=date(2024, 7, 2)
    )
    assert read.data[0].rights_dividend_value == "-1.235"
    assert read.data[0].prior_close == token


def test_nullable_source_field_must_be_present_and_today_is_rejected():
    data = _fixture()
    del data["capital_reduction_results"][0]["ex_right_reference_price"]
    with pytest.raises(TwmdReadError):
        FixtureClient(data).capital_reduction_results(
            "TWSE:123A", "2024-07-01", "2024-07-01", today_taipei=date(2024, 7, 2)
        )
    client = FixtureClient(_fixture())
    with pytest.raises(ValueError):
        client.ex_right_dividend_results("TWSE:123A", "2024-06-25", "2024-06-25", today_taipei=date(2024, 6, 25))
    assert not client.calls
