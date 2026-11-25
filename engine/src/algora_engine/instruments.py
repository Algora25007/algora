"""Exchanges, segments and instrument helpers shared by the DSL, charges and lot-size tables."""

from __future__ import annotations

from enum import StrEnum


class Exchange(StrEnum):
    NSE = "NSE"  # NSE cash market
    BSE = "BSE"  # BSE cash market
    NFO = "NFO"  # NSE equity derivatives
    BFO = "BFO"  # BSE equity derivatives
    MCX = "MCX"  # MCX commodity derivatives


class Segment(StrEnum):
    EQUITY_INTRADAY = "equity_intraday"
    EQUITY_DELIVERY = "equity_delivery"
    FUTURES = "futures"
    OPTIONS = "options"
    COMMODITY_FUTURES = "commodity_futures"
    COMMODITY_OPTIONS = "commodity_options"


#: Which exchanges a segment can trade on.
SEGMENT_EXCHANGES: dict[Segment, frozenset[Exchange]] = {
    Segment.EQUITY_INTRADAY: frozenset({Exchange.NSE, Exchange.BSE}),
    Segment.EQUITY_DELIVERY: frozenset({Exchange.NSE, Exchange.BSE}),
    Segment.FUTURES: frozenset({Exchange.NFO, Exchange.BFO}),
    Segment.OPTIONS: frozenset({Exchange.NFO, Exchange.BFO}),
    Segment.COMMODITY_FUTURES: frozenset({Exchange.MCX}),
    Segment.COMMODITY_OPTIONS: frozenset({Exchange.MCX}),
}

DERIVATIVE_SEGMENTS = frozenset(
    {Segment.FUTURES, Segment.OPTIONS, Segment.COMMODITY_FUTURES, Segment.COMMODITY_OPTIONS}
)
OPTION_SEGMENTS = frozenset({Segment.OPTIONS, Segment.COMMODITY_OPTIONS})

#: MCX symbols the browser app recognises (algora-dash.js isMcxInst).
MCX_SYMBOLS = frozenset(
    {
        "CRUDEOIL",
        "CRUDEOILM",
        "GOLD",
        "GOLDM",
        "GOLDPETAL",
        "SILVER",
        "SILVERM",
        "NATURALGAS",
        "NATGASMINI",
        "COPPER",
        "ZINC",
        "ALUMINIUM",
        "LEAD",
        "NICKEL",
    }
)

#: Index underlyings with exchange-traded derivatives.
NSE_INDEX_SYMBOLS = frozenset({"NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "NIFTYNXT50"})
BSE_INDEX_SYMBOLS = frozenset({"SENSEX", "BANKEX", "SENSEX50"})


def is_mcx_symbol(symbol: str) -> bool:
    return symbol.upper() in MCX_SYMBOLS


def is_index_symbol(symbol: str) -> bool:
    s = symbol.upper()
    return s in NSE_INDEX_SYMBOLS or s in BSE_INDEX_SYMBOLS
