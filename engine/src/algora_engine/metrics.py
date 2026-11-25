"""Backtest metrics computed from closed trades (net of charges unless stated)."""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Sequence

from .trader import ClosedTrade


def _r(v: float) -> float:
    return round(v, 2)


def equity_curve(trades: Sequence[ClosedTrade]) -> list[dict]:
    """Cumulative net P&L after each trade, starting from a zero point."""
    eq, out = 0.0, [{"ts": None, "equity": 0.0}]
    for t in trades:
        eq += t.net_pnl
        out.append({"ts": t.exit_ts.isoformat(timespec="minutes"), "equity": _r(eq)})
    return out


def max_drawdown(values: Sequence[float]) -> float:
    """Largest peak-to-trough fall of a cumulative series that starts at 0 (the peak starts at 0 too)."""
    peak = 0.0
    dd = 0.0
    for v in values:
        peak = max(peak, v)
        dd = max(dd, peak - v)
    return dd


def per_day(trades: Sequence[ClosedTrade]) -> list[dict]:
    days: OrderedDict[str, dict] = OrderedDict()
    for t in trades:
        k = t.day.isoformat()
        row = days.setdefault(k, {"day": k, "trades": 0, "wins": 0, "gross_pnl": 0.0, "charges": 0.0, "net_pnl": 0.0})
        row["trades"] += 1
        row["wins"] += 1 if t.net_pnl > 0 else 0
        row["gross_pnl"] += t.gross_pnl
        row["charges"] += float(t.charges.total) if t.charges else 0.0
        row["net_pnl"] += t.net_pnl
    for row in days.values():
        for k in ("gross_pnl", "charges", "net_pnl"):
            row[k] = _r(row[k])
    return list(days.values())


def summarize(trades: Sequence[ClosedTrade]) -> dict:
    n = len(trades)
    nets = [t.net_pnl for t in trades]
    wins = [v for v in nets if v > 0]
    losses = [v for v in nets if v <= 0]  # break-even counts as a loss, as in the browser backtester
    gross_profit = sum(wins)
    gross_loss = -sum(losses)
    cum, running = [], 0.0
    for v in nets:
        running += v
        cum.append(running)
    streak = worst_streak = 0
    for v in nets:
        streak = streak + 1 if v <= 0 else 0
        worst_streak = max(worst_streak, streak)
    days = per_day(trades)
    day_nets = [d["net_pnl"] for d in days]
    total_charges = sum(float(t.charges.total) for t in trades if t.charges)
    return {
        "trades": n,
        "wins": len(wins),
        "losses": len(losses),
        "win_rate_pct": _r(len(wins) / n * 100) if n else None,
        "gross_pnl": _r(sum(t.gross_pnl for t in trades)),
        "total_charges": _r(total_charges),
        "net_pnl": _r(sum(nets)),
        "gross_profit": _r(gross_profit),
        "gross_loss": _r(gross_loss),
        # None when there are no losing trades (the UI shows ∞ if gross_profit > 0).
        "profit_factor": round(gross_profit / gross_loss, 4) if gross_loss > 0 else None,
        "max_drawdown": _r(max_drawdown(cum)),
        "avg_win": _r(gross_profit / len(wins)) if wins else None,
        "avg_loss": _r(-gross_loss / len(losses)) if losses else None,
        "expectancy": _r(sum(nets) / n) if n else None,
        "best_trade": _r(max(nets)) if n else None,
        "worst_trade": _r(min(nets)) if n else None,
        "max_consecutive_losses": worst_streak,
        "long_trades": sum(1 for t in trades if t.side > 0),
        "short_trades": sum(1 for t in trades if t.side < 0),
        "exit_reasons": dict(sorted(_count(t.exit_reason for t in trades).items())),
        "days_traded": len(days),
        "winning_days": sum(1 for v in day_nets if v > 0),
        "losing_days": sum(1 for v in day_nets if v < 0),
        "best_day": _r(max(day_nets)) if days else None,
        "worst_day": _r(min(day_nets)) if days else None,
        "avg_day": _r(sum(day_nets) / len(days)) if days else None,
    }


def _count(items) -> dict[str, int]:
    out: dict[str, int] = {}
    for i in items:
        out[i] = out.get(i, 0) + 1
    return out
