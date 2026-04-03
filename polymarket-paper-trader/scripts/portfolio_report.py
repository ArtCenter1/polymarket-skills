#!/usr/bin/env python3
"""
Portfolio Performance Report

Generates detailed analytics for a paper trading portfolio:
- Total and annualized return
- Win rate, Sharpe ratio, Sortino ratio
- Max drawdown, average trade duration
- Best/worst trades
- Output as formatted text or JSON
"""

import argparse
import json
import math
import sqlite3
import sys
import os
from datetime import datetime, timezone
from pathlib import Path

# Import from paper_engine (same directory)
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
if _THIS_DIR not in sys.path:
    sys.path.append(_THIS_DIR)
from paper_engine import (
    DB_PATH,
    _get_db,
    _active_portfolio,
    get_portfolio,
)


def generate_report(portfolio_name: str = "default") -> dict:
    """Generate a full performance report for the portfolio."""
    conn = _get_db()
    try:
        pf = _active_portfolio(conn, portfolio_name)
        pid = pf["id"]
        starting = pf["starting_balance"]

        # Get current state with live prices
        current = get_portfolio(portfolio_name, refresh_prices=True)

        # ----- Trade analysis -----
        trades = conn.execute(
            """SELECT * FROM trades WHERE portfolio_id = ?
               ORDER BY executed_at ASC""",
            (pid,),
        ).fetchall()
        trades = [dict(t) for t in trades]

        # Match buys to sells to compute per-trade P&L
        closed_trades = _match_trades(trades)
        open_positions = current["positions"]

        # ----- Daily snapshots -----
        snapshots = conn.execute(
            """SELECT * FROM daily_snapshots WHERE portfolio_id = ?
               ORDER BY date ASC""",
            (pid,),
        ).fetchall()
        snapshots = [dict(s) for s in snapshots]

        # ----- Core metrics -----
        total_value = current["total_value"]
        total_return = (total_value - starting) / starting if starting else 0

        # Time-based calculations
        created = datetime.fromisoformat(pf["created_at"].replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        days_active = max((now - created).days, 1)
        years_active = days_active / 365.25

        annualized_return = (
            ((1 + total_return) ** (1 / years_active) - 1)
            if years_active > 0 and total_return > -1 else 0
        )

        # Win rate
        winning = [t for t in closed_trades if t["pnl"] > 0]
        losing = [t for t in closed_trades if t["pnl"] <= 0]
        win_rate = len(winning) / len(closed_trades) if closed_trades else 0

        # Average P&L
        avg_win = (
            sum(t["pnl"] for t in winning) / len(winning) if winning else 0
        )
        avg_loss = (
            sum(t["pnl"] for t in losing) / len(losing) if losing else 0
        )

        # Profit factor
        gross_profit = sum(t["pnl"] for t in winning)
        gross_loss = abs(sum(t["pnl"] for t in losing))
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")

        # ----- Drawdown from snapshots -----
        equity_curve = [starting]
        if snapshots:
            equity_curve = [s["total_value"] for s in snapshots]
        max_drawdown, max_dd_duration = _compute_drawdown(equity_curve)

        # ----- Sharpe & Sortino from daily returns -----
        daily_returns = _daily_returns(snapshots, starting)
        sharpe = _sharpe_ratio(daily_returns)
        sortino = _sortino_ratio(daily_returns)

        # ----- Average trade duration -----
        durations = []
        for ct in closed_trades:
            if ct.get("open_time") and ct.get("close_time"):
                try:
                    t_open = datetime.fromisoformat(
                        ct["open_time"].replace("Z", "+00:00")
                    )
                    t_close = datetime.fromisoformat(
                        ct["close_time"].replace("Z", "+00:00")
                    )
                    durations.append((t_close - t_open).total_seconds() / 3600)
                except (ValueError, TypeError):
                    pass
        avg_duration_hours = (
            sum(durations) / len(durations) if durations else 0
        )

        # ----- Best / Worst trades -----
        sorted_by_pnl = sorted(closed_trades, key=lambda t: t["pnl"], reverse=True)
        best_trades = sorted_by_pnl[:3] if sorted_by_pnl else []
        worst_trades = sorted_by_pnl[-3:][::-1] if sorted_by_pnl else []

        # ----- Fees -----
        total_fees = sum(t.get("fee", 0) for t in trades)

        report = {
            "portfolio_name": portfolio_name,
            "generated_at": now.isoformat(),
            "days_active": days_active,
            "summary": {
                "starting_balance": starting,
                "current_value": total_value,
                "cash_balance": current["cash_balance"],
                "positions_value": current["positions_value"],
                "total_return_usd": round(total_value - starting, 2),
                "total_return_pct": round(total_return * 100, 2),
                "annualized_return_pct": round(annualized_return * 100, 2),
            },
            "risk_metrics": {
                "sharpe_ratio": round(sharpe, 3),
                "sortino_ratio": round(sortino, 3),
                "max_drawdown_pct": round(max_drawdown * 100, 2),
                "max_drawdown_duration_days": max_dd_duration,
                "current_drawdown_pct": current["drawdown_pct"],
            },
            "trade_metrics": {
                "total_trades": len(trades),
                "closed_trades": len(closed_trades),
                "open_positions": len(open_positions),
                "win_rate_pct": round(win_rate * 100, 1),
                "avg_win_usd": round(avg_win, 2),
                "avg_loss_usd": round(avg_loss, 2),
                "profit_factor": round(profit_factor, 2),
                "total_fees_usd": round(total_fees, 2),
                "avg_trade_duration_hours": round(avg_duration_hours, 1),
            },
            "best_trades": [
                _trade_summary(t) for t in best_trades
            ],
            "worst_trades": [
                _trade_summary(t) for t in worst_trades
            ],
            "open_positions": [
                {
                    "market": p["market_question"],
                    "side": p["side"],
                    "shares": p["shares"],
                    "entry": p["avg_entry"],
                    "current": p["current_price"],
                    "unrealized_pnl": p["unrealized_pnl"],
                }
                for p in open_positions
            ],
        }

        return report

    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Analytics helpers
# ---------------------------------------------------------------------------

def _match_trades(trades: list[dict]) -> list[dict]:
    """
    Match BUY and SELL trades on the same token/side to compute
    per-round-trip P&L.
    """
    # Group buys by (token_id, side)
    open_lots: dict[tuple, list] = {}
    closed: list[dict] = []

    for t in trades:
        key = (t["token_id"], t["side"])

        if t["action"] == "BUY":
            if key not in open_lots:
                open_lots[key] = []
            open_lots[key].append({
                "shares": t["shares"],
                "price": t["price"],
                "fee": t["fee"],
                "time": t["executed_at"],
                "market": t.get("market_question", ""),
                "reasoning": t.get("reasoning", ""),
            })

        elif t["action"] == "SELL":
            lots = open_lots.get(key, [])
            remaining = t["shares"]
            sell_price = t["price"]
            sell_fee = t["fee"]
            sell_time = t["executed_at"]

            while remaining > 0.0001 and lots:
                lot = lots[0]
                matched = min(remaining, lot["shares"])

                pnl = (sell_price - lot["price"]) * matched - (
                    lot["fee"] * (matched / lot["shares"]) if lot["shares"] > 0 else 0
                ) - (
                    sell_fee * (matched / t["shares"]) if t["shares"] > 0 else 0
                )

                closed.append({
                    "token_id": t["token_id"],
                    "side": t["side"],
                    "market": lot["market"],
                    "shares": round(matched, 4),
                    "entry_price": lot["price"],
                    "exit_price": sell_price,
                    "pnl": round(pnl, 4),
                    "pnl_pct": round(
                        (sell_price - lot["price"]) / lot["price"] * 100, 2
                    ) if lot["price"] > 0 else 0,
                    "open_time": lot["time"],
                    "close_time": sell_time,
                    "reasoning": lot["reasoning"],
                })

                lot["shares"] -= matched
                remaining -= matched
                if lot["shares"] < 0.0001:
                    lots.pop(0)

    return closed


def _compute_drawdown(equity_curve: list[float]) -> tuple[float, int]:
    """Compute max drawdown and its duration in days."""
    if not equity_curve or len(equity_curve) < 2:
        return 0.0, 0

    peak = equity_curve[0]
    max_dd = 0.0
    dd_start = 0
    max_dd_duration = 0
    current_dd_start = 0

    for i, value in enumerate(equity_curve):
        if value >= peak:
            peak = value
            duration = i - current_dd_start
            max_dd_duration = max(max_dd_duration, duration)
            current_dd_start = i
        else:
            dd = (peak - value) / peak
            if dd > max_dd:
                max_dd = dd
                dd_start = current_dd_start

    # Check if still in drawdown
    if equity_curve[-1] < peak:
        duration = len(equity_curve) - 1 - current_dd_start
        max_dd_duration = max(max_dd_duration, duration)

    return max_dd, max_dd_duration


def _daily_returns(
    snapshots: list[dict],
    starting_balance: float,
) -> list[float]:
    """Extract daily return series from snapshots."""
    if not snapshots:
        return []

    values = [starting_balance] + [s["total_value"] for s in snapshots]
    returns = []
    for i in range(1, len(values)):
        if values[i - 1] > 0:
            returns.append((values[i] - values[i - 1]) / values[i - 1])
    return returns


def _sharpe_ratio(returns: list[float], risk_free_rate: float = 0.0) -> float:
    """Compute annualized Sharpe ratio (assumes daily data)."""
    if len(returns) < 2:
        return 0.0
    import statistics
    avg_return = statistics.mean(returns)
    std_dev = statistics.stdev(returns)
    if std_dev == 0:
        return 0.0
    return (avg_return - (risk_free_rate / 365.25)) / std_dev * math.sqrt(365.25)


def _sortino_ratio(returns: list[float], risk_free_rate: float = 0.0) -> float:
    """Compute annualized Sortino ratio (assumes daily data)."""
    if len(returns) < 2:
        return 0.0
    import statistics
    avg_return = statistics.mean(returns)
    downside_returns = [r for r in returns if r < 0]
    if not downside_returns:
        return float("inf") if avg_return > 0 else 0.0
    downside_std = math.sqrt(sum(r**2 for r in downside_returns) / len(returns))
    if downside_std == 0:
        return 0.0
    return (avg_return - (risk_free_rate / 365.25)) / downside_std * math.sqrt(365.25)


def _trade_summary(t: dict) -> dict:
    """Compact summary for best/worst trades."""
    return {
        "market": t["market"],
        "side": t["side"],
        "pnl": t["pnl"],
        "pnl_pct": t["pnl_pct"],
        "duration_hours": round(
            (datetime.fromisoformat(t["close_time"].replace("Z", "+00:00")) -
             datetime.fromisoformat(t["open_time"].replace("Z", "+00:00"))).total_seconds() / 3600,
            1
        ) if t.get("open_time") and t.get("close_time") else 0
    }


def _format_report(report: dict) -> str:
    """Format report dict as readable text."""
    s = report["summary"]
    r = report["risk_metrics"]
    t = report["trade_metrics"]

    out = [
        f"=== Portfolio Report: {report['portfolio_name']} ===",
        f"Generated: {report['generated_at']}",
        f"Days Active: {report['days_active']}",
        "",
        "--- Summary ---",
        f"Starting Balance: ${s['starting_balance']:,.2f}",
        f"Current Value:    ${s['current_value']:,.2f}",
        f"Total Return:     ${s['total_return_usd']:+,.2f} ({s['total_return_pct']:+.2f}%)",
        f"Annualized:       {s['annualized_return_pct']:+.2f}%",
        "",
        "--- Risk Metrics ---",
        f"Sharpe Ratio:     {r['sharpe_ratio']}",
        f"Sortino Ratio:    {r['sortino_ratio']}",
        f"Max Drawdown:     {r['max_drawdown_pct']}%",
        f"Max DD Duration:  {r['max_drawdown_duration_days']} days",
        "",
        "--- Trade Metrics ---",
        f"Total Trades:     {t['total_trades']} ({t['closed_trades']} closed)",
        f"Win Rate:         {t['win_rate_pct']}%",
        f"Profit Factor:    {t['profit_factor']}",
        f"Avg Win/Loss:     ${t['avg_win_usd']:,.2f} / ${t['avg_loss_usd']:,.2f}",
        f"Avg Duration:     {t['avg_trade_duration_hours']} hours",
        f"Total Fees:       ${t['total_fees_usd']:,.2f}",
        "",
    ]

    if report["best_trades"]:
        out.append("--- Best Trades ---")
        for bt in report["best_trades"]:
            out.append(f"  {bt['pnl_pct']:+6.1f}% | ${bt['pnl']:+8.2f} | {bt['market'][:50]}")
        out.append("")

    if report["worst_trades"]:
        out.append("--- Worst Trades ---")
        for wt in report["worst_trades"]:
            out.append(f"  {wt['pnl_pct']:+6.1f}% | ${wt['pnl']:+8.2f} | {wt['market'][:50]}")
        out.append("")

    if report["open_positions"]:
        out.append("--- Open Positions ---")
        for p in report["open_positions"]:
            out.append(f"  {p['side']:3} | {p['shares']:8.2f} | P&L: ${p['unrealized_pnl']:+8.2f} | {p['market'][:50]}")

    return "\n".join(out)


def main():
    parser = argparse.ArgumentParser(description="Generate portfolio performance report")
    parser.add_argument("--name", default="default", help="Portfolio name")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    args = parser.parse_args()

    try:
        report = generate_report(args.name)
        if args.json:
            print(json.dumps(report, indent=2))
        else:
            print(_format_report(report))
    except (RuntimeError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
