#!/usr/bin/env python3
"""
Polymarket Paper Trading Engine

Simulates trades against live Polymarket data with zero financial risk.
Uses SQLite for persistent storage across agent sessions.
Fetches real prices from the CLOB and Gamma APIs.
"""

import argparse
import json
import os
import sqlite3
import sys
import time
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import urlopen, Request
from urllib.error import URLError, HTTPError

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DB_DIR = Path.home() / ".polymarket-paper"
DB_PATH = DB_DIR / "portfolio.db"
GAMMA_API = "https://gamma-api.polymarket.com"
CLOB_API = "https://clob.polymarket.com"
DEFAULT_BALANCE = 1000.0

# Risk defaults (overridable per-portfolio)
DEFAULT_RISK = {
    "max_position_pct": 0.10,       # 10% of bankroll per trade
    "max_drawdown_pct": 0.30,       # 30% total drawdown halts trading
    "max_concurrent_positions": 5,
    "daily_loss_limit_pct": 0.05,   # 5% of starting bankroll
    "max_single_market_pct": 0.20,  # 20% portfolio in one market
    "human_approval_pct": 0.15,     # trades > 15% need human approval
}

# Polymarket fee tiers — most markets are fee-free.
DEFAULT_FEE_RATE = 0.0

# Token ID format: numeric string, typically 50-100 digits
_TOKEN_ID_RE = re.compile(r"^\d{20,120}$")


def _validate_token_id(token_id: str) -> str:
    """Validate a CLOB token ID before using it in URLs."""
    if not isinstance(token_id, str) or not _TOKEN_ID_RE.match(token_id):
        raise ValueError(
            f"Invalid token ID format: must be 20-120 digits, got: {token_id!r}"
        )
    return token_id


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

def _api_get(url: str, timeout: int = 15) -> dict | list:
    """GET JSON from a URL. Returns parsed JSON."""
    req = Request(url, headers={"User-Agent": "polymarket-paper-trader/1.0"})
    try:
        with urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except (URLError, HTTPError) as exc:
        raise RuntimeError(f"API request failed: {url} — {exc}") from exc


def fetch_orderbook(token_id: str) -> dict:
    """Fetch the live order book for a CLOB token."""
    _validate_token_id(token_id)
    return _api_get(f"{CLOB_API}/book?token_id={token_id}")


def fetch_midpoint(token_id: str) -> float:
    """Fetch the midpoint price for a token."""
    _validate_token_id(token_id)
    data = _api_get(f"{CLOB_API}/midpoint?token_id={token_id}")
    return float(data["mid"])


def fetch_price(token_id: str, side: str) -> float:
    """Fetch the best price for a side (buy/sell)."""
    _validate_token_id(token_id)
    data = _api_get(f"{CLOB_API}/price?token_id={token_id}&side={side}")
    return float(data["price"])


def lookup_market(token_id: str) -> dict | None:
    """Look up market metadata by CLOB token ID via Gamma API."""
    _validate_token_id(token_id)
    data = _api_get(
        f"{GAMMA_API}/markets?clob_token_ids={token_id}&limit=1"
    )
    if data and len(data) > 0:
        return data[0]
    return None


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

def _get_db() -> sqlite3.Connection:
    """Open (and possibly initialize) the SQLite database."""
    DB_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    _init_schema(conn)
    return conn


def _init_schema(conn: sqlite3.Connection):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS portfolios (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            name          TEXT NOT NULL DEFAULT 'default',
            starting_balance REAL NOT NULL,
            cash_balance  REAL NOT NULL,
            peak_value    REAL NOT NULL,
            created_at    TEXT NOT NULL,
            updated_at    TEXT NOT NULL,
            risk_config   TEXT NOT NULL,
            active        INTEGER NOT NULL DEFAULT 1
        );

        CREATE TABLE IF NOT EXISTS positions (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            portfolio_id  INTEGER NOT NULL REFERENCES portfolios(id),
            token_id      TEXT NOT NULL,
            market_question TEXT,
            side          TEXT NOT NULL CHECK(side IN ('YES','NO')),
            shares        REAL NOT NULL DEFAULT 0,
            avg_entry     REAL NOT NULL DEFAULT 0,
            current_price REAL NOT NULL DEFAULT 0,
            opened_at     TEXT NOT NULL,
            updated_at    TEXT NOT NULL,
            closed        INTEGER NOT NULL DEFAULT 0,
            closed_at     TEXT,
            UNIQUE(portfolio_id, token_id, side, closed)
        );

        CREATE TABLE IF NOT EXISTS trades (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            portfolio_id  INTEGER NOT NULL REFERENCES portfolios(id),
            token_id      TEXT NOT NULL,
            market_question TEXT,
            side          TEXT NOT NULL CHECK(side IN ('YES','NO')),
            action        TEXT NOT NULL CHECK(action IN ('BUY','SELL')),
            shares        REAL NOT NULL,
            price         REAL NOT NULL,
            fee           REAL NOT NULL DEFAULT 0,
            total_cost    REAL NOT NULL,
            reasoning     TEXT,
            executed_at   TEXT NOT NULL,
            entry_avg     REAL
        );

        CREATE TABLE IF NOT EXISTS daily_snapshots (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            portfolio_id  INTEGER NOT NULL REFERENCES portfolios(id),
            date          TEXT NOT NULL,
            cash_balance  REAL NOT NULL,
            positions_value REAL NOT NULL,
            total_value   REAL NOT NULL,
            daily_pnl     REAL NOT NULL DEFAULT 0,
            UNIQUE(portfolio_id, date)
        );
    """)
    conn.commit()


# ---------------------------------------------------------------------------
# Portfolio operations
# ---------------------------------------------------------------------------

def init_portfolio(
    starting_balance: float = DEFAULT_BALANCE,
    name: str = "default",
    risk_config: dict | None = None,
) -> dict:
    """Create a new paper-trading portfolio."""
    if starting_balance <= 0:
        raise ValueError("Starting balance must be positive")

    risk = {**DEFAULT_RISK, **(risk_config or {})}
    now = datetime.now(timezone.utc).isoformat()

    conn = _get_db()
    try:
        # Deactivate existing portfolios with the same name
        conn.execute(
            "UPDATE portfolios SET active = 0 WHERE name = ? AND active = 1",
            (name,),
        )
        cur = conn.execute(
            """INSERT INTO portfolios
               (name, starting_balance, cash_balance, peak_value,
                created_at, updated_at, risk_config, active)
               VALUES (?, ?, ?, ?, ?, ?, ?, 1)""",
            (name, starting_balance, starting_balance, starting_balance,
             now, now, json.dumps(risk)),
        )
        conn.commit()
        pid = cur.lastrowid
    finally:
        conn.close()

    return {
        "portfolio_id": pid,
        "name": name,
        "starting_balance": starting_balance,
        "cash_balance": starting_balance,
        "positions": [],
        "total_value": starting_balance,
        "pnl": 0.0,
        "pnl_pct": 0.0,
        "created_at": now,
    }


def _active_portfolio(conn: sqlite3.Connection, name: str = "default") -> dict:
    """Fetch the active portfolio row or raise."""
    row = conn.execute(
        "SELECT * FROM portfolios WHERE name = ? AND active = 1 ORDER BY id DESC LIMIT 1",
        (name,),
    ).fetchone()
    if not row:
        raise RuntimeError(
            f"No active portfolio '{name}'. Run: python paper_engine.py --action init"
        )
    return dict(row)


def get_portfolio(name: str = "default", refresh_prices: bool = True) -> dict:
    """Return the current portfolio state with live-priced positions."""
    conn = _get_db()
    try:
        pf = _active_portfolio(conn, name)
        pid = pf["id"]

        positions = conn.execute(
            "SELECT * FROM positions WHERE portfolio_id = ? AND closed = 0",
            (pid,),
        ).fetchall()

        pos_list = []
        positions_value = 0.0
        for p in positions:
            p = dict(p)
            if refresh_prices:
                try:
                    p["current_price"] = fetch_midpoint(p["token_id"])
                    conn.execute(
                        "UPDATE positions SET current_price = ?, updated_at = ? WHERE id = ?",
                        (p["current_price"],
                         datetime.now(timezone.utc).isoformat(), p["id"]),
                    )
                except Exception:
                    pass  # keep stale price
            value = p["shares"] * p["current_price"]
            unrealized_pnl = (p["current_price"] - p["avg_entry"]) * p["shares"]
            pos_list.append({
                "token_id": p["token_id"],
                "market_question": p["market_question"],
                "side": p["side"],
                "shares": p["shares"],
                "avg_entry": p["avg_entry"],
                "current_price": p["current_price"],
                "value": round(value, 4),
                "unrealized_pnl": round(unrealized_pnl, 4),
                "opened_at": p["opened_at"],
            })
            positions_value += value

        total_value = pf["cash_balance"] + positions_value
        starting = pf["starting_balance"]
        pnl = total_value - starting

        # Update peak
        if total_value > pf["peak_value"]:
            conn.execute(
                "UPDATE portfolios SET peak_value = ?, updated_at = ? WHERE id = ?",
                (total_value, datetime.now(timezone.utc).isoformat(), pid),
            )
            pf["peak_value"] = total_value

        drawdown = (pf["peak_value"] - total_value) / pf["peak_value"] if pf["peak_value"] > 0 else 0

        return {
            "name": name,
            "starting_balance": starting,
            "cash_balance": pf["cash_balance"],
            "positions_value": round(positions_value, 4),
            "total_value": round(total_value, 4),
            "pnl": round(pnl, 4),
            "pnl_pct": round((pnl / starting) * 100, 2) if starting else 0,
            "drawdown_pct": round(drawdown * 100, 2),
            "num_open_positions": len(pos_list),
            "positions": pos_list,
            "created_at": pf["created_at"],
        }
    finally:
        conn.close()


def place_order(
    token_id: str,
    side: str,
    size: float,
    price: float | None = None,
    reasoning: str = "",
    portfolio_name: str = "default",
    fee_rate: float = DEFAULT_FEE_RATE,
    force: bool = False,
) -> dict:
    """Place a paper trade."""
    side = side.upper()
    if side not in ("YES", "NO"):
        raise ValueError(f"Side must be YES or NO, got: {side}")
    if size <= 0:
        raise ValueError("Size must be positive")

    # Fetch market data and simulate fill BEFORE acquiring the write lock
    market_info = lookup_market(token_id)
    market_question = market_info["question"] if market_info else "Unknown market"

    if price is not None:
        shares = size / price
        fee = size * fee_rate
        fill = {
            "avg_price": price,
            "shares_filled": round(shares, 4),
            "total_cost": round(size + fee, 4),
            "fee": round(fee, 4),
        }
    else:
        orderbook = fetch_orderbook(token_id)
        fill = _simulate_fill(orderbook, "BUY", size, fee_rate)

    # Get portfolio state for risk checks
    portfolio_state = get_portfolio(portfolio_name, refresh_prices=True)

    conn = _get_db()
    try:
        # Acquire exclusive write lock for atomic balance check + debit
        conn.execute("BEGIN IMMEDIATE")

        pf = _active_portfolio(conn, portfolio_name)
        pid = pf["id"]
        risk_config = json.loads(pf["risk_config"])

        # Balance check
        if fill["total_cost"] > pf["cash_balance"]:
            conn.rollback()
            raise RuntimeError(
                f"Insufficient balance: need ${fill['total_cost']:.2f}, "
                f"have ${pf['cash_balance']:.2f}"
            )

        # Risk validation
        if not force:
            ok, reason = _validate_risk(
                portfolio_state, risk_config, "BUY", size, token_id
            )
            if not ok:
                conn.rollback()
                raise RuntimeError(f"Risk check failed: {reason}")

            ok, reason = _check_daily_loss(
                conn, pid, pf["starting_balance"], risk_config
            )
            if not ok:
                conn.rollback()
                raise RuntimeError(f"Risk check failed: {reason}")

        now = datetime.now(timezone.utc).isoformat()

        # Update or create position
        existing = conn.execute(
            """SELECT * FROM positions
               WHERE portfolio_id = ? AND token_id = ? AND side = ? AND closed = 0""",
            (pid, token_id, side),
        ).fetchone()

        if existing:
            existing = dict(existing)
            old_shares = existing["shares"]
            old_avg = existing["avg_entry"]
            new_shares = old_shares + fill["shares_filled"]
            new_avg = (
                (old_avg * old_shares + fill["avg_price"] * fill["shares_filled"])
                / new_shares
            )
            conn.execute(
                """UPDATE positions
                   SET shares = ?, avg_entry = ?, current_price = ?,
                       updated_at = ?
                   WHERE id = ?""",
                (round(new_shares, 4), round(new_avg, 6),
                 fill["avg_price"], now, existing["id"]),
            )
        else:
            conn.execute(
                """INSERT INTO positions
                   (portfolio_id, token_id, market_question, side, shares,
                    avg_entry, current_price, opened_at, updated_at, closed)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0)""",
                (pid, token_id, market_question, side,
                 fill["shares_filled"], fill["avg_price"],
                 fill["avg_price"], now, now),
            )

        # Deduct from balance
        new_balance = pf["cash_balance"] - fill["total_cost"]
        conn.execute(
            "UPDATE portfolios SET cash_balance = ?, updated_at = ? WHERE id = ?",
            (round(new_balance, 4), now, pid),
        )

        # Record trade with entry_avg snapshot for daily loss tracking
        conn.execute(
            """INSERT INTO trades
               (portfolio_id, token_id, market_question, side, action,
                shares, price, fee, total_cost, reasoning, executed_at,
                entry_avg)
               VALUES (?, ?, ?, ?, 'BUY', ?, ?, ?, ?, ?, ?, ?)""",
            (pid, token_id, market_question, side,
             fill["shares_filled"], fill["avg_price"], fill["fee"],
             fill["total_cost"], reasoning, now, fill["avg_price"]),
        )

        conn.commit()

        return {
            "status": "filled",
            "action": "BUY",
            "side": side,
            "token_id": token_id,
            "market": market_question,
            "shares": fill["shares_filled"],
            "avg_price": fill["avg_price"],
            "fee": fill["fee"],
            "total_cost": fill["total_cost"],
            "new_balance": round(new_balance, 4),
            "reasoning": reasoning,
            "executed_at": now,
        }
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def close_position(
    token_id: str,
    side: str | None = None,
    portfolio_name: str = "default",
    fee_rate: float = DEFAULT_FEE_RATE,
    reasoning: str = "",
) -> dict:
    """Close an open position at current market price."""
    orderbook = fetch_orderbook(token_id)
    bids = sorted(
        orderbook.get("bids", []),
        key=lambda x: float(x["price"]),
        reverse=True,
    )
    if not bids:
        raise RuntimeError("No bids in order book — cannot close position")

    conn = _get_db()
    try:
        conn.execute("BEGIN IMMEDIATE")
        pf = _active_portfolio(conn, portfolio_name)
        pid = pf["id"]

        if side:
            side = side.upper()
            positions = conn.execute(
                """SELECT * FROM positions
                   WHERE portfolio_id = ? AND token_id = ? AND side = ? AND closed = 0""",
                (pid, token_id, side),
            ).fetchall()
        else:
            positions = conn.execute(
                """SELECT * FROM positions
                   WHERE portfolio_id = ? AND token_id = ? AND closed = 0""",
                (pid, token_id),
            ).fetchall()

        if not positions:
            conn.rollback()
            raise RuntimeError(f"No open position for token {token_id}" + (f" side={side}" if side else ""))

        results = []
        for pos in positions:
            pos = dict(pos)
            remaining_shares = pos["shares"]
            total_proceeds = 0.0
            for level in bids:
                lvl_price = float(level["price"])
                lvl_size = float(level["size"])
                sell_shares = min(remaining_shares, lvl_size)
                total_proceeds += sell_shares * lvl_price
                remaining_shares -= sell_shares
                if remaining_shares < 0.0001:
                    break

            shares_sold = pos["shares"] - remaining_shares
            if shares_sold <= 0:
                continue

            avg_sell_price = total_proceeds / shares_sold
            fee = total_proceeds * fee_rate
            net_proceeds = total_proceeds - fee
            pnl = (avg_sell_price - pos["avg_entry"]) * shares_sold - fee
            now = datetime.now(timezone.utc).isoformat()

            conn.execute("UPDATE positions SET closed = 1, closed_at = ?, updated_at = ? WHERE id = ?", (now, now, pos["id"]))
            new_balance = pf["cash_balance"] + net_proceeds
            conn.execute("UPDATE portfolios SET cash_balance = ?, updated_at = ? WHERE id = ?", (round(new_balance, 4), now, pid))
            pf["cash_balance"] = new_balance

            conn.execute(
                """INSERT INTO trades
                   (portfolio_id, token_id, market_question, side, action,
                    shares, price, fee, total_cost, reasoning, executed_at, entry_avg)
                   VALUES (?, ?, ?, ?, 'SELL', ?, ?, ?, ?, ?, ?, ?)""",
                (pid, token_id, pos["market_question"], pos["side"],
                 shares_sold, avg_sell_price, fee, total_proceeds, reasoning, now, pos["avg_entry"]),
            )

            results.append({
                "side": pos["side"],
                "shares_sold": round(shares_sold, 4),
                "avg_sell_price": round(avg_sell_price, 4),
                "realized_pnl": round(pnl, 4),
                "new_balance": round(new_balance, 4),
            })

        conn.commit()
        return results[0] if len(results) == 1 else results
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _validate_risk(pf: dict, risk: dict, action: str, size: float, token_id: str) -> tuple[bool, str]:
    """Validate a trade against risk limits."""
    if action == "BUY":
        if size > pf["total_value"] * risk["max_position_pct"]:
            return False, f"Position size ${size:.2f} exceeds {risk['max_position_pct']:.0%} limit"
        if pf["drawdown_pct"] > risk["max_drawdown_pct"] * 100:
            return False, f"Drawdown {pf['drawdown_pct']}% exceeds {risk['max_drawdown_pct']:.0%} limit"
        if pf["num_open_positions"] >= risk["max_concurrent_positions"]:
            # Check if we already have this token
            exists = any(p["token_id"] == token_id for p in pf["positions"])
            if not exists:
                return False, f"Max concurrent positions ({risk['max_concurrent_positions']}) reached"
    return True, ""


def _check_daily_loss(conn: sqlite3.Connection, pid: int, starting: float, risk: dict) -> tuple[bool, str]:
    """Check if daily loss limit has been reached."""
    today = datetime.now(timezone.utc).date().isoformat()
    limit = starting * risk["daily_loss_limit_pct"]
    
    # Calculate realized P&L today using entry_avg stored at trade time
    row = conn.execute(
        """SELECT SUM(
            CASE WHEN action='SELL' THEN (price - entry_avg) * shares - fee
                 ELSE -fee END
        ) as daily_realized
        FROM trades
        WHERE portfolio_id = ? AND date(executed_at) = ?""",
        (pid, today),
    ).fetchone()
    
    realized = row["daily_realized"] or 0
    if realized < -limit:
        return False, f"Daily loss ${abs(realized):.2f} exceeds limit ${limit:.2f}"
    return True, ""


def _simulate_fill(orderbook: dict, action: str, size_usd: float, fee_rate: float) -> dict:
    """Simulate walking the order book for a market order."""
    if action == "BUY":
        levels = sorted(orderbook.get("asks", []), key=lambda x: float(x["price"]))
    else:
        levels = sorted(orderbook.get("bids", []), key=lambda x: float(x["price"]), reverse=True)

    if not levels:
        raise RuntimeError(f"No liquidity on {action} side")

    remaining = size_usd
    shares = 0.0
    total_cost = 0.0
    for lvl in levels:
        p = float(lvl["price"])
        s = float(lvl["size"])
        fill_usd = min(remaining, s * p)
        shares += fill_usd / p
        total_cost += fill_usd
        remaining -= fill_usd
        if remaining < 0.01:
            break

    if remaining > 1.0: # more than $1 unfilled
        raise RuntimeError(f"Insufficient liquidity: could only fill ${size_usd - remaining:.2f} of ${size_usd:.2f}")

    fee = total_cost * fee_rate
    return {
        "avg_price": total_cost / shares if shares > 0 else 0,
        "shares_filled": round(shares, 4),
        "total_cost": round(total_cost + fee, 4),
        "fee": round(fee, 4),
    }


def take_snapshot(portfolio_name: str = "default") -> dict:
    """Record a daily snapshot of the portfolio value."""
    pf = get_portfolio(portfolio_name, refresh_prices=True)
    today = datetime.now(timezone.utc).date().isoformat()
    
    conn = _get_db()
    try:
        # Get yesterday's value
        prev = conn.execute(
            "SELECT total_value FROM daily_snapshots WHERE portfolio_id = (SELECT id FROM portfolios WHERE name = ? AND active = 1) ORDER BY date DESC LIMIT 1",
            (portfolio_name,)
        ).fetchone()
        
        prev_val = prev["total_value"] if prev else pf["starting_balance"]
        daily_pnl = pf["total_value"] - prev_val
        
        pid = conn.execute("SELECT id FROM portfolios WHERE name = ? AND active = 1", (portfolio_name,)).fetchone()["id"]
        
        conn.execute(
            """INSERT OR REPLACE INTO daily_snapshots
               (portfolio_id, date, cash_balance, positions_value, total_value, daily_pnl)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (pid, today, pf["cash_balance"], pf["positions_value"], pf["total_value"], daily_pnl)
        )
        conn.commit()
        return {
            "date": today,
            "total_value": pf["total_value"],
            "daily_pnl": daily_pnl
        }
    finally:
        conn.close()


def get_trades(name: str = "default", limit: int = 50) -> list:
    """Return recent trade history."""
    conn = _get_db()
    try:
        pf = _active_portfolio(conn, name)
        rows = conn.execute(
            "SELECT * FROM trades WHERE portfolio_id = ? ORDER BY executed_at DESC LIMIT ?",
            (pf["id"], limit)
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def _format_portfolio(p: dict) -> str:
    out = [
        f"=== Portfolio: {p['name']} ===",
        f"Starting Balance:  $ {p['starting_balance']:10,.2f}",
        f"Cash Balance:      $ {p['cash_balance']:10,.2f}",
        f"Positions Value:   $ {p['positions_value']:10,.2f}",
        f"Total Value:       $ {p['total_value']:10,.2f}",
        f"P&L:               $ {p['pnl']:10,.2f} ({p['pnl_pct']:+.2f}%)",
        f"Drawdown:                {p['drawdown_pct']:>9.2f}%",
        f"Open Positions:             {p['num_open_positions']}",
        f"Created:           {p['created_at']}",
    ]
    if p["positions"]:
        out.append("--- Open Positions ---")
        for pos in p["positions"]:
            out.append(
                f"  {pos['side']:3} {pos['shares']:8.2f} shares @ ${pos['avg_entry']:.4f} -> ${pos['current_price']:.4f}  P&L: ${pos['unrealized_pnl']:+,.2f}"
            )
            out.append(f"      {pos['market_question']}")
    return "\n".join(out)


def _format_trades(trades: list) -> str:
    if not trades: return "No trades found."
    out = ["--- Trade History ---"]
    for t in trades:
        out.append(
            f"{t['executed_at'][:19]} | {t['action']:4} {t['side']:3} | {t['shares']:8.2f} @ ${t['price']:.4f} | Cost: ${t['total_cost']:8.2f} | {t['market_question'][:40]}"
        )
    return "\n".join(out)


def main():
    parser = argparse.ArgumentParser(description="Polymarket Paper Trading Engine")
    parser.add_argument("--action", choices=["init", "buy", "sell", "close", "portfolio", "trades", "snapshot"], required=True)
    parser.add_argument("--balance", type=float, default=DEFAULT_BALANCE)
    parser.add_argument("--name", default="default")
    parser.add_argument("--token", help="CLOB token ID")
    parser.add_argument("--side", choices=["YES", "NO", "yes", "no"])
    parser.add_argument("--size", type=float)
    parser.add_argument("--price", type=float)
    parser.add_argument("--reason", default="")
    parser.add_argument("--fee-rate", type=float, default=DEFAULT_FEE_RATE)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--limit", type=int, default=50)
    args = parser.parse_args()

    try:
        if args.action == "init":
            res = init_portfolio(args.balance, args.name)
            print(json.dumps(res, indent=2) if args.json else f"Portfolio '{res['name']}' initialized.")
        elif args.action in ("buy", "sell"):
            if not args.token or not args.side or not args.size:
                parser.error("--token, --side, and --size required")
            res = place_order(args.token, args.side, args.size, args.price, args.reason, args.name, args.fee_rate, args.force)
            print(json.dumps(res, indent=2) if args.json else f"Filled {res['action']} {res['side']} {res['shares']} shares.")
        elif args.action == "close":
            if not args.token: parser.error("--token required")
            res = close_position(args.token, args.side, args.name, args.fee_rate, args.reason)
            print(json.dumps(res, indent=2) if args.json else "Position closed.")
        elif args.action == "portfolio":
            res = get_portfolio(args.name)
            print(json.dumps(res, indent=2) if args.json else _format_portfolio(res))
        elif args.action == "trades":
            res = get_trades(args.name, args.limit)
            print(json.dumps(res, indent=2) if args.json else _format_trades(res))
        elif args.action == "snapshot":
            res = take_snapshot(args.name)
            print(json.dumps(res, indent=2) if args.json else f"Snapshot for {res['date']} taken.")
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
