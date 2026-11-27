import pytest

from algora_engine.browser_import import (
    bot_key,
    clean_accounts,
    convert_bot,
    instrument_for,
    split_account_label,
)
from algora_engine.instruments import Exchange, Segment

BOT = {
    "name": "Nifty FCB",
    "strategy": "First Candle Cross",
    "inst": "NIFTY",
    "acctLabel": "Zerodha · Main",
    "tf": "15m",
    "buf": 0.1,
    "exit": "target",
    "exitv": 1.5,
    "sq": "15:15",
    "max": 2,
    "qty": 1,
    "paused": True,
}


def test_convert_target_bot():
    c = convert_bot(BOT)
    s = c.spec
    assert c.paused and c.account_label == "Zerodha · Main"
    assert s.template == "first_candle_cross" and s.timeframe == "15m"
    assert s.exits.target_pct == 1.5 and s.exits.stop_pct is None and s.exits.flip
    assert s.risk.max_entries_per_day == 2 and s.risk.quantity_lots == 1
    assert s.instrument.exchange is Exchange.NFO and s.session.start == "09:15"
    assert s.entry.long[0].right.offset_pct == 0.1


def test_convert_stop_and_flip_bots():
    assert convert_bot({**BOT, "exit": "stop", "exitv": 0.5}).spec.exits.stop_pct == 0.5
    s = convert_bot({**BOT, "exit": "flip", "exitv": 0}).spec
    assert s.exits.target_pct is None and s.exits.stop_pct is None
    mcx = convert_bot({**BOT, "inst": "CRUDEOIL", "sq": "23:15"}).spec
    assert mcx.instrument.exchange is Exchange.MCX and mcx.session.start == "09:00"


@pytest.mark.parametrize(
    ("patch", "reason"),
    [
        ({"name": " "}, "missing name"),
        ({"strategy": "Martingale"}, "unknown strategy"),
        ({"strategy": "Monthly option spread"}, "not available.*option-chain"),
        ({"inst": "nifty"}, "invalid instrument"),
        ({"tf": "1h"}, "invalid timeframe"),
        ({"buf": 9}, "buffer"),
        ({"buf": True}, "buffer"),
        ({"exit": "trail"}, "invalid exit"),
        ({"exitv": 30}, "exit %"),
        ({"sq": "3pm"}, "square-off"),
        ({"max": 1.5}, "max entries"),
        ({"qty": 0}, "quantity"),
        ({"sq": "09:00"}, "cannot convert: Value error, square_off must be after start"),
    ],
)
def test_convert_rejects(patch, reason):
    with pytest.raises(ValueError, match=reason):
        convert_bot({**BOT, **patch})


def test_convert_rejects_non_object():
    with pytest.raises(ValueError):
        convert_bot("bot")


def test_accounts_and_labels():
    acc = clean_accounts({"zerodha": [" Main ", "Main", "", 5, "x" * 60], "unknown": ["a"], "dhan": "nope"})
    assert acc["zerodha"] == ["Main", "x" * 40] and acc["dhan"] == [] and "unknown" not in acc
    assert clean_accounts(None)["upstox"] == []
    assert split_account_label("Angel One · Swing") == ("angelone", "Swing")
    assert split_account_label("Nobody · x") is None
    assert split_account_label("Zerodha") is None
    assert split_account_label("Zerodha · ") is None


def test_instrument_mapping_and_key():
    assert instrument_for("SENSEX").exchange is Exchange.BFO
    assert instrument_for("GOLDM").segment is Segment.COMMODITY_FUTURES
    assert instrument_for("RELIANCE").segment is Segment.FUTURES
    assert bot_key(BOT) == "Nifty FCB|First Candle Cross|NIFTY|Zerodha · Main|15m|0.1|target|1.5|15:15|2|1"


def test_convert_opening_range_breakout_bot():
    c = convert_bot({**BOT, "strategy": "Opening Range Breakout", "tf": "5m", "buf": 0.2, "exitv": 1.0})
    s = c.spec
    assert s.template == "opening_range_breakout" and s.timeframe == "5m"
    assert s.session.opening_range_minutes == 30 and s.session.square_off == "15:15"
    assert s.entry.long[0].right.ref == "range_high" and s.entry.long[0].right.offset_pct == 0.2
    assert s.exits.target_pct == 1.0 and s.exits.stop_pct is None and s.exits.stop_level is None and s.exits.flip
    assert s.risk.max_entries_per_day == 2
    assert c.defaults_used == {"opening_range_minutes": 30, "buffer_type": "pct", "direction": "both"}
    assert any("Reverses" in n for n in c.notes)
    s = convert_bot({**BOT, "strategy": "Opening Range Breakout", "exit": "stop", "exitv": 0.5}).spec
    assert s.exits.stop_pct == 0.5 and s.exits.target_pct is None


def test_convert_gap_fade_bot():
    c = convert_bot({**BOT, "strategy": "Gap fade", "tf": "5m", "exit": "flip", "exitv": 0})
    s = c.spec
    assert s.template == "gap_fade" and s.session.signals_from == "session_start"
    assert s.exits.stop_pct == 1.0 and s.exits.target_level.long.kind == "prev_session"
    assert c.defaults_used["min_gap_pct"] == 0.5 and c.defaults_used["target_type"] == "prev_close"
    assert any("buffer ignored" in n for n in c.notes) and any("Flip has no meaning" in n for n in c.notes)
    c = convert_bot({**BOT, "strategy": "Gap fade", "buf": 0})
    assert c.spec.exits.target_pct == 1.5 and c.spec.exits.stop_pct == 1.0 and c.defaults_used["stop_value"] == 1.0
    assert not any("buffer" in n for n in c.notes)
    c = convert_bot({**BOT, "strategy": "Gap fade", "exit": "stop", "exitv": 0.8})
    assert c.spec.exits.stop_pct == 0.8 and c.spec.exits.target_level is not None


def test_convert_momentum_bot():
    c = convert_bot({**BOT, "strategy": "Momentum", "exit": "stop", "exitv": 0.7})
    s = c.spec
    assert s.template == "momentum" and s.timeframe == "15m"
    assert (s.entry.long[0].left.length, s.entry.long[0].right.length) == (9, 21)
    assert s.exits.stop_pct == 0.7 and s.exits.flip and s.exits.conditions is not None
    assert c.defaults_used["signal"] == "ema_cross"
    assert any("positional rotation" in n for n in c.notes) and any("buffer ignored" in n for n in c.notes)
    mcx = convert_bot({**BOT, "strategy": "Momentum", "inst": "GOLDM", "sq": "23:00", "buf": 0}).spec
    assert mcx.session.start == "09:00" and mcx.exits.target_pct == 1.5


def test_template_conversion_errors_are_reported():
    with pytest.raises(ValueError, match="cannot convert"):
        convert_bot({**BOT, "strategy": "Opening Range Breakout", "sq": "09:30"})  # range ends after square-off
