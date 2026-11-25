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
        ({"strategy": "Gap fade"}, "not available"),
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
