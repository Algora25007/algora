"""Convert the static site's localStorage export (``algora-backup.json``, version 1) into DSL strategies.

The browser validates bots with ``cleanBot`` in index.html; the same rules are applied here so anything the site
would have accepted converts, and anything it would have dropped is reported with a reason.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from pydantic import ValidationError

from .dsl import Instrument, StrategySpec
from .instruments import BSE_INDEX_SYMBOLS, Exchange, Segment, is_mcx_symbol
from .templates import build_template, first_candle_cross

BROWSER_STRATEGIES = ("First Candle Cross", "Opening Range Breakout", "Gap fade", "Monthly option spread", "Momentum")
#: Browser strategy name -> engine template id.
SUPPORTED = {
    "First Candle Cross": "first_candle_cross",
    "Opening Range Breakout": "opening_range_breakout",
    "Gap fade": "gap_fade",
    "Momentum": "momentum",
}
NOT_AVAILABLE_REASON = {"Monthly option spread": "it needs option-chain data the engine does not have yet"}
BROKER_IDS = (
    "zerodha",
    "upstox",
    "angelone",
    "dhan",
    "fyers",
    "groww",
    "kotaksecurities",
    "5paisa",
    "aliceblueonline",
    "shoonya",
    "motilaloswal",
    "icicidirect",
)
BROKER_NAMES = {
    "zerodha": "Zerodha",
    "upstox": "Upstox",
    "angelone": "Angel One",
    "dhan": "Dhan",
    "fyers": "Fyers",
    "groww": "Groww",
    "kotaksecurities": "Kotak Securities",
    "5paisa": "5paisa",
    "aliceblueonline": "Alice Blue",
    "shoonya": "Shoonya",
    "motilaloswal": "Motilal Oswal",
    "icicidirect": "ICICI Direct",
}
_TIME = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
_SYM = re.compile(r"^[A-Z0-9&-]{1,20}$")


def _num(v: Any, lo: float, hi: float) -> bool:
    return isinstance(v, int | float) and not isinstance(v, bool) and lo <= v <= hi and v == v


@dataclass(frozen=True)
class ConvertedBot:
    spec: StrategySpec
    paused: bool
    account_label: str | None  # "Zerodha · Main" in the browser; split into broker id + label by the caller
    #: Template parameters the browser bot did not have, filled with defaults (name -> value).
    defaults_used: dict[str, Any] = field(default_factory=dict)
    #: How the browser settings were interpreted, in plain words.
    notes: tuple[str, ...] = ()


def clean_label(v: Any) -> str | None:
    return v.strip()[:40] if isinstance(v, str) and v.strip() else None


def clean_accounts(raw: Any) -> dict[str, list[str]]:
    """Same rules as the browser's cleanAccounts: known brokers only, trimmed unique labels, 50 per broker."""
    out: dict[str, list[str]] = {}
    src = raw if isinstance(raw, dict) else {}
    for bid in BROKER_IDS:
        labels: list[str] = []
        items = src.get(bid)
        for item in items if isinstance(items, list) else []:
            label = clean_label(item)
            if label and label not in labels:
                labels.append(label)
        out[bid] = labels[:50]
    return out


def split_account_label(acct_label: str) -> tuple[str, str] | None:
    """Map the browser's "Broker name · label" back to (broker_id, label)."""
    name, sep, label = acct_label.partition(" · ")
    if not sep:
        return None
    for bid, bname in BROKER_NAMES.items():
        if bname == name.strip() and label.strip():
            return bid, label.strip()[:40]
    return None


def instrument_for(symbol: str) -> Instrument:
    """Browser bots trade in lots, so they are F&O (or MCX) contracts. Futures are assumed (see docs)."""
    if is_mcx_symbol(symbol):
        return Instrument(symbol=symbol, exchange=Exchange.MCX, segment=Segment.COMMODITY_FUTURES)
    if symbol in BSE_INDEX_SYMBOLS:
        return Instrument(symbol=symbol, exchange=Exchange.BFO, segment=Segment.FUTURES)
    return Instrument(symbol=symbol, exchange=Exchange.NFO, segment=Segment.FUTURES)


def convert_bot(raw: Any) -> ConvertedBot:
    """Validate one browser bot and convert it. Raises ValueError with a user-facing reason."""
    if not isinstance(raw, dict):
        raise ValueError("not an object")
    name = raw.get("name")
    if not isinstance(name, str) or not name.strip():
        raise ValueError("missing name")
    strategy = raw.get("strategy")
    if strategy not in BROWSER_STRATEGIES:
        raise ValueError("unknown strategy")
    if strategy not in SUPPORTED:
        raise ValueError(f"{strategy} is not available in the engine yet: {NOT_AVAILABLE_REASON[strategy]}")
    inst = raw.get("inst")
    if not isinstance(inst, str) or not _SYM.match(inst):
        raise ValueError("invalid instrument")
    if raw.get("tf") not in ("5m", "15m", "30m"):
        raise ValueError("invalid timeframe")
    if not _num(raw.get("buf"), 0, 5):
        raise ValueError("entry buffer must be 0 to 5")
    exit_mode = raw.get("exit")
    if exit_mode not in ("flip", "target", "stop"):
        raise ValueError("invalid exit")
    if exit_mode != "flip" and not _num(raw.get("exitv"), 0.05, 20):
        raise ValueError("exit % must be 0.05 to 20")
    sq = raw.get("sq")
    if not isinstance(sq, str) or not _TIME.match(sq):
        raise ValueError("invalid square-off time")
    mx, qty = raw.get("max"), raw.get("qty")
    if not _num(mx, 1, 50) or mx % 1:
        raise ValueError("max entries must be a whole number 1 to 50")
    if not _num(qty, 1, 500) or qty % 1:
        raise ValueError("quantity must be a whole number of lots 1 to 500")
    acct = raw.get("acctLabel")
    instrument = instrument_for(inst)
    start = "09:00" if instrument.exchange is Exchange.MCX else "09:15"
    exitv = float(raw["exitv"]) if exit_mode != "flip" else None
    defaults: dict[str, Any] = {}
    notes: list[str] = []
    try:
        if strategy == "First Candle Cross":
            spec = first_candle_cross(
                name=name.strip()[:80],
                instrument=instrument,
                timeframe=raw["tf"],
                buffer_pct=float(raw["buf"]),
                start=start,
                square_off=sq,
                target_pct=exitv if exit_mode == "target" else None,
                stop_pct=exitv if exit_mode == "stop" else None,
                flip=True,  # the browser always reversed on an opposite cross, whatever the exit mode
                max_entries_per_day=int(mx),
                quantity_lots=int(qty),
            )
        else:
            params, defaults, notes = _template_params(strategy, raw, exit_mode, exitv)
            common = {
                "timeframe": raw["tf"],
                "start": start,
                "square_off": sq,
                "max_entries_per_day": int(mx),
                "quantity_lots": int(qty),
            }
            spec = build_template(
                SUPPORTED[strategy], name=name.strip()[:80], instrument=instrument, params={**common, **params}
            )
    except (ValidationError, ValueError) as e:
        raise ValueError(f"cannot convert: {_first_error(e)}") from e
    return ConvertedBot(
        spec=spec,
        paused=raw.get("paused") is True,
        account_label=acct[:100] if isinstance(acct, str) else None,
        defaults_used=defaults,
        notes=tuple(notes),
    )


#: Defaults for settings a browser bot does not have. Reported back to the user on import.
IMPORT_DEFAULTS: dict[str, dict[str, Any]] = {
    "Opening Range Breakout": {"opening_range_minutes": 30, "buffer_type": "pct", "direction": "both"},
    "Gap fade": {
        "min_gap_pct": 0.5,
        "max_gap_pct": 3.0,
        "fade": "both",
        "require_unfilled": True,
        "entry_end": "45 min after the session start",
    },
    "Momentum": {"signal": "ema_cross", "fast_length": 9, "slow_length": 21, "vwap_filter": False, "direction": "both"},
}


def _template_params(
    strategy: str, raw: dict, exit_mode: str, exitv: float | None
) -> tuple[dict[str, Any], dict[str, Any], list[str]]:
    """Template parameters for a non-FCB browser bot: (params, defaults used, notes)."""
    buf = float(raw["buf"])
    notes: list[str] = []
    defaults = dict(IMPORT_DEFAULTS[strategy])
    if strategy == "Opening Range Breakout":
        params: dict[str, Any] = {
            "opening_range_minutes": 30,
            "buffer_type": "pct",
            "buffer": buf,
            "direction": "both",
            "flip": True,
            "stop_type": "pct" if exit_mode == "stop" else "none",
            "stop_value": exitv if exit_mode == "stop" else None,
            "target_type": "pct" if exit_mode == "target" else "none",
            "target_value": exitv if exit_mode == "target" else None,
        }
        notes.append("Entry buffer kept as a % beyond the opening range high / low.")
        notes.append("Reverses on the opposite breakout, as the browser did; only the exit you chose is set.")
        return params, defaults, notes
    if strategy == "Gap fade":
        params = {"min_gap_pct": 0.5, "max_gap_pct": 3.0, "fade": "both", "require_unfilled": True}
        if buf:
            notes.append("Entry buffer ignored: the gap fade has no entry buffer (minimum gap 0.5% used instead).")
        if exit_mode == "target":
            params |= {"target_type": "pct", "target_value": exitv}
            defaults |= {"stop_type": "pct", "stop_value": 1.0}
        elif exit_mode == "stop":
            params |= {"stop_type": "pct", "stop_value": exitv}
            defaults |= {"target_type": "prev_close"}
        else:
            defaults |= {"stop_type": "pct", "stop_value": 1.0, "target_type": "prev_close"}
            notes.append("Flip has no meaning for a gap fade: default exits used (1% stop, target the previous close).")
        return params, defaults, notes
    # Momentum
    params = {
        "signal": "ema_cross",
        "fast_length": 9,
        "slow_length": 21,
        "vwap_filter": False,
        "direction": "both",
        "flip": True,
        "exit_on_opposite_signal": True,
        "stop_type": "pct" if exit_mode == "stop" else "none",
        "stop_value": exitv if exit_mode == "stop" else None,
        "target_type": "pct" if exit_mode == "target" else "none",
        "target_value": exitv if exit_mode == "target" else None,
    }
    if buf:
        notes.append("Entry buffer ignored: the EMA crossover has no entry buffer.")
    notes.append(
        "Imported as intraday momentum (9/21 EMA crossover on the bot's timeframe, squared off daily); the site "
        "described Momentum as a positional rotation, which the engine does not have."
    )
    return params, defaults, notes


def bot_key(raw: dict) -> str:
    """The browser's botKey, used to skip duplicates on re-import."""
    keys = ("name", "strategy", "inst", "acctLabel", "tf", "buf", "exit", "exitv", "sq", "max", "qty")
    return "|".join(str(raw.get(k)) for k in keys)


def _first_error(e: Exception) -> str:
    if isinstance(e, ValidationError):
        err = e.errors()[0]
        return str(err.get("msg"))
    return str(e)
