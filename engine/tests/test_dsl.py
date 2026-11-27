import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from algora_engine.dsl import (
    SpecVersionError,
    StrategySpec,
    hhmm_to_minutes,
    json_schema,
    load_spec,
)
from algora_engine.templates import first_candle_cross

SCHEMA_FILE = Path(__file__).resolve().parents[1] / "schema" / "strategy.v2.json"

VALID = {
    "schema_version": 1,
    "name": " Nifty FCB ",
    "instrument": {"symbol": "NIFTY", "exchange": "NFO", "segment": "futures"},
    "timeframe": "15m",
    "session": {"start": "09:15", "square_off": "15:15"},
    "entry": {
        "long": [
            {
                "op": "crosses_above",
                "left": {"kind": "price", "field": "close"},
                "right": {"kind": "session", "ref": "first_candle_high", "offset_pct": 0.1},
            }
        ]
    },
    "exits": {"target_pct": 1.5, "stop_pct": 0.5, "flip": False},
    "risk": {"max_entries_per_day": 2, "quantity_lots": 3},
}


def test_valid_spec_round_trip():
    s = load_spec(VALID)
    assert s.name == "Nifty FCB"
    assert s.exits.stop_pct == 0.5 and s.exits.target_pct == 1.5  # stop and target together
    assert s.timeframe_minutes == 15
    assert StrategySpec.model_validate_json(s.model_dump_json()) == s


@pytest.mark.parametrize(
    ("path", "value", "msg"),
    [
        (("instrument", "exchange"), "MCX", "trades on BFO, NFO"),
        (("instrument", "symbol"), "nifty", "pattern"),
        (("session", "square_off"), "09:00", "square_off must be after start"),
        (("session", "start"), "9:15", "HH:MM"),
        (("exits", "stop_pct"), 0.01, "greater than or equal"),
        (("exits", "trailing_stop_pct"), 25, "less than or equal"),
        (("risk", "quantity_lots"), 0, "greater than or equal"),
        (("risk", "max_entries_per_day"), 51, "less than or equal"),
        (("timeframe",), "2m", "Input should be"),
        (("name",), "   ", "blank"),
        (("entry",), {"long": [], "short": []}, "at least one"),
        (("unknown_field",), 1, "Extra inputs"),
    ],
)
def test_invalid_specs(path, value, msg):
    doc = json.loads(json.dumps(VALID))
    target = doc
    for k in path[:-1]:
        target = target[k]
    target[path[-1]] = value
    with pytest.raises(ValidationError, match=msg):
        load_spec(doc)


def test_delivery_rejected_in_v1():
    doc = json.loads(json.dumps(VALID))
    doc["instrument"] = {"symbol": "INFY", "exchange": "NSE", "segment": "equity_delivery"}
    with pytest.raises(ValidationError, match="intraday"):
        load_spec(doc)


def test_agri_only_on_mcx():
    doc = json.loads(json.dumps(VALID))
    doc["instrument"]["agri"] = True
    with pytest.raises(ValidationError, match="agri"):
        load_spec(doc)


def test_version_handling():
    with pytest.raises(SpecVersionError, match="required"):
        load_spec({k: v for k, v in VALID.items() if k != "schema_version"})
    with pytest.raises(SpecVersionError, match="unsupported"):
        load_spec({**VALID, "schema_version": 3})
    with pytest.raises(SpecVersionError, match="unsupported"):
        load_spec({**VALID, "schema_version": True})
    with pytest.raises(SpecVersionError):
        load_spec([])  # type: ignore[arg-type]


def test_hhmm():
    assert hhmm_to_minutes("23:59") == 1439
    with pytest.raises(ValueError):
        hhmm_to_minutes("24:00")


def test_committed_json_schema_is_current():
    """Schema changes must be deliberate: regenerate with `python -m algora_engine.schema_export`."""
    assert json.loads(SCHEMA_FILE.read_text()) == json_schema()


def test_template_builds_expected_rules(unit_future):
    s = first_candle_cross(name="x", instrument=unit_future, buffer_pct=0.2, stop_pct=1, target_pct=2)
    assert s.template == "first_candle_cross"
    assert s.entry.long[0].right.offset_pct == 0.2
    assert s.entry.short[0].right.offset_pct == -0.2
    assert s.entry.short[0].op == "crosses_below"


def test_schema_export_writes_file(tmp_path):
    from algora_engine.schema_export import main

    out = tmp_path / "s.json"
    assert main([str(out)]) == 0
    assert json.loads(out.read_text()) == json_schema()


def test_instrument_helpers():
    from algora_engine.instruments import is_index_symbol, is_mcx_symbol

    assert is_index_symbol("sensex") and is_index_symbol("NIFTY") and not is_index_symbol("TCS")
    assert is_mcx_symbol("goldm") and not is_mcx_symbol("NIFTY")
