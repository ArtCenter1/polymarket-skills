#!/usr/bin/env python3
"""Analyze paper trading performance and suggest parameter adjustments.

Reads the paper trader's SQLite database, computes performance metrics,
breaks down results by strategy type, and outputs actionable suggestions.

Usage:
    python daily_review.py --portfolio-db ~/.polymarket-paper/portfolio.db
    python daily_review.py --portfolio-db ~/.polymarket-paper/portfolio.db --days 7
"""

import argparse
import json
import math
import os
import sqlite3
import sys
from datetime import datetime, timedelta, timezone


DEFAULT_DB_PATH = os.path.expanduser("~/.polymarket-paper/portfolio.db")


def connect_db(db_path):
    """Connect to the paper trader database. Returns None if not found."""
    if not os.path.exists(db_path):
        return None
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def get_closed_trades(conn, since_date):
    """Fetch all closed (SELL) trades since a given date."""
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT
                t.market_question,
                t.side,
                t.action,
                t.price as exit_price,
                t.shares,
                t.total_cost,
                t.fee,
                t.reasoning,
                t.executed_at as closed_at,
                t.entry_avg as entry_price
            FROM trades t
            WHERE t.action = 'SELL'
              AND t.executed_at >= ?
            ORDER BY t.executed_at DESC
            """,
            (since_date.isoformat(),),
        )
        rows = [dict(row) for row in cur.fetchall()]
        # Calculate realized P&L for each trade
        for r in rows:
            entry = float(r.get("entry_price", 0))
            exit_p = float(r.get("exit_price", 0))
            shares = float(r.get("shares", 0))
            fee = float(r.get("fee", 0))
            r["realized_pnl"] = round((exit_p - entry) * shares - fee, 4)
        return rows
    except sqlite3.OperationalError:
        return []


def get_open_positions(conn):
    """Fetch currently open positions."""
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT
                market_question,
                token_id,
                side,
                avg_entry as entry_price,
                shares,
                current_price,
                opened_at
            FROM positions
            WHERE closed = 0
            ORDER BY opened_at DESC
            """
        )
        return [dict(row) for row in cur.fetchall()]
    except sqlite3.OperationalError:
        return []


def get_account_history(conn, since_date):
    """Fetch portfolio value history from daily_snapshots."""
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT total_value as portfolio_value, cash_balance as cash, date as updated_at
            FROM daily_snapshots
            WHERE date >= ?
            ORDER BY date ASC
            """,
            (since_date.strftime("%Y-%m-%d"),),
        )
        return [dict(row) for row in cur.fetchall()]
    except sqlite3.OperationalError:
        return []


def compute_metrics(trades):
    """Compute performance metrics from a list of closed trades."""
    if not trades:
        return {
            "total_trades": 0,
            "winners": 0,
            "losers": 0,
            "win_rate": 0.0,
            "total_pnl": 0.0,
            "avg_pnl": 0.0,
            "avg_winner": 0.0,
            "avg_loser": 0.0,
            "largest_winner": 0.0,
            "largest_loser": 0.0,
            "profit_factor": 0.0,
            "avg_hold_time_hours": 0.0,
        }

    pnls = [float(t.get("realized_pnl", 0)) for t in trades]
    winners = [p for p in pnls if p > 0]
    losers = [p for p in pnls if p < 0]
    total_pnl = sum(pnls)

    gross_profit = sum(winners) if winners else 0
    gross_loss = abs(sum(losers)) if losers else 0

    return {
        "total_trades": len(trades),
        "winners": len(winners),
        "losers": len(losers),
        "breakeven": len(trades) - len(winners) - len(losers),
        "win_rate": len(winners) / len(trades) if trades else 0,
        "total_pnl": round(total_pnl, 2),
        "avg_pnl": round(total_pnl / len(trades), 2) if trades else 0,
        "avg_winner": round(gross_profit / len(winners), 2) if winners else 0,
        "avg_loser": round(sum(losers) / len(losers), 2) if losers else 0,
        "largest_winner": round(max(winners), 2) if winners else 0,
        "largest_loser": round(min(losers), 2) if losers else 0,
        "profit_factor": round(gross_profit / gross_loss, 2) if gross_loss > 0 else float("inf"),
        "avg_hold_time_hours": 0.0, # Not easily computable from current schema without JOIN
    }


def compute_drawdown(account_history):
    """Compute max drawdown from account history."""
    if not account_history:
        return {"max_drawdown_pct": 0, "current_drawdown_pct": 0}

    values = [float(h["portfolio_value"]) for h in account_history]
    if not values:
        return {"max_drawdown_pct": 0, "current_drawdown_pct": 0}
        
    peak = values[0]
    max_dd = 0
    for v in values:
        peak = max(peak, v)
        dd = (peak - v) / peak if peak > 0 else 0
        max_dd = max(max_dd, dd)

    current_peak = max(values)
    current_dd = (current_peak - values[-1]) / current_peak if current_peak > 0 else 0

    return {
        "max_drawdown_pct": round(max_dd * 100, 2),
        "current_drawdown_pct": round(current_dd * 100, 2),
    }


def generate_suggestions(metrics, drawdown, open_positions):
    """Generate actionable parameter adjustment suggestions."""
    suggestions = []

    if metrics["total_trades"] == 0:
        suggestions.append(
            "No closed trades in this period. Start by running the advisor "
            "to find opportunities and executing paper trades."
        )
        return suggestions

    if metrics["win_rate"] < 0.40 and metrics["total_trades"] >= 10:
        suggestions.append(
            f"Win rate is {metrics['win_rate']:.0%} (below 40% threshold). "
            f"Consider tightening entry criteria: increase --min-edge to 0.05 "
            f"or raise minimum confidence to 0.7."
        )
    elif metrics["win_rate"] > 0.70 and metrics["total_trades"] >= 10:
        suggestions.append(
            f"Win rate is {metrics['win_rate']:.0%} (strong). Consider "
            f"slightly increasing position sizes if risk limits allow."
        )

    if metrics["profit_factor"] < 1.0 and metrics["total_trades"] >= 5:
        suggestions.append(
            f"Profit factor is {metrics['profit_factor']:.2f} (below 1.0 = "
            f"losing money). Review entry/exit strategies."
        )

    if drawdown["current_drawdown_pct"] > 15:
        suggestions.append(
            f"Current drawdown is {drawdown['current_drawdown_pct']:.1f}%. "
            f"Reduce position sizes immediately."
        )

    if len(open_positions) >= 5:
        suggestions.append(
            f"Currently at {len(open_positions)} open positions (maximum). "
            f"Close existing positions before opening new ones."
        )

    if not suggestions:
        suggestions.append(
            "Performance looks healthy. Continue with current parameters."
        )

    return suggestions


def main():
    parser = argparse.ArgumentParser(description="Polymarket Performance Review")
    parser.add_argument("--portfolio-db", default=DEFAULT_DB_PATH, help="Path to portfolio.db")
    parser.add_argument("--days", type=int, default=7, help="Number of days to review")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    args = parser.parse_args()

    conn = connect_db(args.portfolio_db)
    if not conn:
        print(f"Error: Database not found at {args.portfolio_db}", file=sys.stderr)
        sys.exit(1)

    since_date = datetime.now(timezone.utc) - timedelta(days=args.days)
    
    trades = get_closed_trades(conn, since_date)
    open_pos = get_open_positions(conn)
    history = get_account_history(conn, since_date)
    
    metrics = compute_metrics(trades)
    drawdown = compute_drawdown(history)
    suggestions = generate_suggestions(metrics, drawdown, open_pos)
    
    review = {
        "review_date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "period_days": args.days,
        "metrics": metrics,
        "drawdown": drawdown,
        "open_positions_count": len(open_pos),
        "suggestions": suggestions
    }

    if args.json:
        print(json.dumps(review, indent=2))
    else:
        print(f"=== Polymarket Daily Review ({args.days} days) ===")
        print(f"Trades: {metrics['total_trades']} | Win Rate: {metrics['win_rate']:.1%}")
        print(f"Total P&L: ${metrics['total_pnl']:,.2f} | Profit Factor: {metrics['profit_factor']}")
        print(f"Max Drawdown: {drawdown['max_drawdown_pct']}%")
        print("\n--- Suggestions ---")
        for s in suggestions:
            print(f"- {s}")

    conn.close()


if __name__ == "__main__":
    main()
