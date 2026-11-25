from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from algora_engine.candles import Bar
from algora_engine.dsl import Instrument
from algora_engine.instruments import Exchange, Segment


def make_bars(day: str, start: str, step_min: int, ohlc: list[tuple[float, float, float, float]]) -> list[Bar]:
    """Consecutive candles from ``day start`` every ``step_min`` minutes."""
    t0 = datetime.fromisoformat(f"{day}T{start}")
    return [Bar(t0 + timedelta(minutes=i * step_min), o, h, lo, c) for i, (o, h, lo, c) in enumerate(ohlc)]


@pytest.fixture
def unit_future() -> Instrument:
    """An NFO future with a lot of 1 unit so P&L equals points."""
    return Instrument(symbol="NIFTY", exchange=Exchange.NFO, segment=Segment.FUTURES, lot_size=1)
