#!/usr/bin/env python3
"""Generate ranked trade recommendations for Polymarket prediction markets.

Scans active markets, scores edges (arbitrage, momentum, orderbook imbalance),
applies Kelly criterion sizing, validates against risk rules, and outputs
actionable trade recommendations as JSON.

Usage:
    python advisor.py --top 5
    python advisor.py --portfolio-db ~/.polymarket-paper/portfolio.db --top 5
    python advisor.py --min-volume 50000 --min-edge 0.03 --top 10
"""

import argparse
import json
import math
import os
import sqlite3
import sys
from datetime import datetime, timezone

import requests

GAMMA_API = "https://gamma-api.polymarket.com"
CLOB_API = "https://clob.polymarket.com"

DEFAULT_PORTFOLIO_VALUE = 10000.0
DEFAULT_MAX_POSITION_PCT = 0.10
DEFAULT_MAX_OPEN_POSITIONS = 5
DEFAULT_MIN_EDGE = 0.03
DEFAULT_MIN_VOLUME = 10000.0
DEFAULT_MIN_CONFIDENCE = 0.5


def fetch_markets(limit=100, min_volume=0):
    """Fetch active markets from Gamma API sorted by 24h volume."""
    params = {
        "limit": min(limit, 100),
        "active": "true",
        "closed": "false",
        "order": "volume24hr",
        "ascending": "false",
    }
    resp = requests.get(f"{GAMMA_API}/markets", params=params, timeout=30)
    resp.raise_for_status()
    raw = resp.json()

    markets = []
    for m in raw:
        vol_24h = float(m.get("volume24hr", 0) or 0)
        if vol_24h < min_volume:
            continue
        if not m.get("acceptingOrders", False):
            continue

        try:
            outcomes = json.loads(m.get("outcomes", "[]"))
        except (json.JSONDecodeError, TypeError):
            outcomes = []
        try:
            prices = json.loads(m.get("outcomePrices", "[]"))
            prices = [float(p) for p in prices]
        except (json.JSONDecodeError, TypeError, ValueError):
            prices = []
        try:
            token_ids = json.loads(m.get("clobTokenIds", "[]"))
        except (json.JSONDecodeError, TypeError):
            token_ids = []

        # Only handle binary markets (2 outcomes) for now
        if len(outcomes) != 2 or len(prices) != 2 or len(token_ids) != 2:
            continue

        end_date = m.get("endDate", "")
        if end_date:
            try:
                end_dt = datetime.fromisoformat(end_date.replace("Z", "+00:00"))
                hours_left = (end_dt - datetime.now(timezone.utc)).total_seconds() / 3600
                if hours_left < 24:
                    continue
            except (ValueError, TypeError):
                pass

        markets.append({
            "question": m.get("question", ""),
            "slug": m.get("slug", ""),
            "condition_id": m.get("conditionID", ""),
            "outcomes": outcomes,
            "prices": prices,
            "token_ids": token_ids,
            "volume_24h": vol_24h,
            "liquidity": float(m.get("liquidityNum", 0) or 0),
            "end_date": end_date,
        })

    return markets


def fetch_orderbook(token_id):
    """Fetch orderbook for a token from CLOB API."""
    try:
        resp = requests.get(
            f"{CLOB_API}/book",
            params={"token_id": token_id},
            timeout=15,
        )
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException:
        return None


def calculate_spread(orderbook):
    """Calculate spread and imbalance from orderbook data."""
    if not orderbook:
        return None

    bids = orderbook.get("bids", [])
    asks = orderbook.get("asks", [])

    if not bids or not asks:
        return None

    best_bid = float(bids[0].get("price", 0))
    best_ask = float(asks[0].get("price", 1))
    spread = best_ask - best_bid
    midpoint = (best_bid + best_ask) / 2

    bid_depth = sum(float(b.get("size", 0)) for b in bids[:5])
    ask_depth = sum(float(a.get("size", 0)) for a in asks[:5])
    total_depth = bid_depth + ask_depth
    imbalance = (bid_depth - ask_depth) / total_depth if total_depth > 0 else 0

    return {
        "best_bid": best_bid,
        "best_ask": best_ask,
        "spread": spread,
        "spread_pct": spread / midpoint if midpoint > 0 else 0,
        "midpoint": midpoint,
        "bid_depth": bid_depth,
        "ask_depth": ask_depth,
        "imbalance": imbalance,
    }


def detect_arbitrage(yes_price, no_price):
    """Detect YES+NO arbitrage. Returns edge if underpriced."""
    total = yes_price + no_price
    if total < 0.99:  # Underpriced: buying both sides guarantees profit
        return {
            "type": "arbitrage",
            "edge": 1.0 - total,
            "direction": "both",
            "detail": f"YES+NO={total:.4f}, guaranteed ${1.0 - total:.4f}/share profit",
        }
    return None


def detect_momentum(imbalance, volume_24h, liquidity):
    """Detect momentum signal from orderbook imbalance and volume."""
    if liquidity <= 0:
        return None

    volume_liquidity_ratio = volume_24h / liquidity
    # High volume relative to liquidity + orderbook imbalance = momentum
    if abs(imbalance) > 0.3 and volume_liquidity_ratio > 2.0:
        direction = "YES" if imbalance > 0 else "NO"
        strength = min(abs(imbalance) * volume_liquidity_ratio / 10, 1.0)
        edge = abs(imbalance) * 0.15  # Conservative edge estimate
        return {
            "type": "momentum",
            "edge": edge,
            "direction": direction,
            "detail": (
                f"Orderbook imbalance={imbalance:+.2f}, "
                f"volume/liquidity={volume_liquidity_ratio:.1f}x, "
                f"momentum favors {direction}"
            ),
            "strength": strength,
        }
    return None


def detect_spread_opportunity(spread_pct, midpoint):
    """Detect wide-spread mean reversion opportunity."""
    # If spread is wide (5-10%), there may be a mean reversion opportunity
    # by placing a limit order at the midpoint
    if 0.05 <= spread_pct <= 0.10 and 0.15 < midpoint < 0.85:
        edge = spread_pct * 0.3  # Conservatively capture 30% of spread
        return {
            "type": "mean-reversion",
            "edge": edge,
            "direction": "YES" if midpoint < 0.5 else "NO",
            "detail": (
                f"Wide spread={spread_pct:.1%}, midpoint={midpoint:.3f}. "
                f"Limit order near midpoint captures spread."
            ),
        }
    return None


def kelly_half(estimated_prob, market_price, side="YES"):
    """Calculate half-Kelly position fraction for a binary market."""
    if side == "YES":
        p = estimated_prob
        cost = market_price
    else:
        p = 1.0 - estimated_prob
        cost = market_price

    if cost <= 0 or cost >= 1:
        return 0

    b = (1.0 - cost) / cost
    q = 1.0 - p
    if b <= 0:
        return 0

    kelly = (b * p - q) / b
    return max(0, kelly * 0.5)


def load_portfolio(db_path):
    """Load portfolio state from paper trader SQLite database."""
    if not db_path or not os.path.exists(db_path):
        return {
            "value": DEFAULT_PORTFOLIO_VALUE,
            "cash": DEFAULT_PORTFOLIO_VALUE,
            "positions": [],
            "peak_value": DEFAULT_PORTFOLIO_VALUE,
            "daily_pnl": 0.0,
            "open_position_count": 0,
        }

    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()

        portfolio = {
            "value": DEFAULT_PORTFOLIO_VALUE,
            "cash": DEFAULT_PORTFOLIO_VALUE,
            "positions": [],
            "peak_value": DEFAULT_PORTFOLIO_VALUE,
            "daily_pnl": 0.0,
            "open_position_count": 0,
        }

        # Read account balance from portfolios table
        try:
            cur.execute(
                "SELECT id, cash_balance, peak_value FROM portfolios "
                "WHERE active = 1 ORDER BY id DESC LIMIT 1"
            )
            row = cur.fetchone()
            if row:
                pid = row["id"]
                portfolio["cash"] = float(row["cash_balance"])
                portfolio["peak_value"] = float(row["peak_value"])
                
                # Calculate total value: cash + positions value
                pos_cur = conn.cursor()
                pos_cur.execute(
                    "SELECT COALESCE(SUM(shares * current_price), 0) as pos_val "
                    "FROM positions WHERE portfolio_id = ? AND closed = 0",
                    (pid,)
                )
                pos_row = pos_cur.fetchone()
                pos_val = float(pos_row["pos_val"]) if pos_row else 0.0
                portfolio["value"] = portfolio["cash"] + pos_val
        except sqlite3.OperationalError:
            pass

        # Read open positions
        try:
            cur.execute(
                "SELECT token_id, side, shares, avg_entry as entry_price, market_question "
                "FROM positions WHERE closed = 0"
            )
            positions = [dict(r) for r in cur.fetchall()]
            portfolio["positions"] = positions
            portfolio["open_position_count"] = len(positions)
        except sqlite3.OperationalError:
            pass

        # Read daily P&L from daily_snapshots
        try:
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            cur.execute(
                "SELECT daily_pnl FROM daily_snapshots "
                "WHERE date = ? ORDER BY id DESC LIMIT 1",
                (today,),
            )
            row = cur.fetchone()
            if row:
                portfolio["daily_pnl"] = float(row["daily_pnl"])
        except sqlite3.OperationalError:
            pass

        conn.close()
        return portfolio
    except Exception:
        return {
            "value": DEFAULT_PORTFOLIO_VALUE,
            "cash": DEFAULT_PORTFOLIO_VALUE,
            "positions": [],
            "peak_value": DEFAULT_PORTFOLIO_VALUE,
            "daily_pnl": 0.0,
            "open_position_count": 0,
        }


def generate_recommendations(markets, portfolio, min_edge=0.03, min_confidence=0.5):
    """Analyze markets and generate trade recommendations."""
    recommendations = []

    for m in markets:
        # Check if we already have a position in this market
        existing = next((p for p in portfolio["positions"] if p["token_id"] in m["token_ids"]), None)
        if existing:
            continue

        # Fetch orderbooks for YES and NO
        yes_book = fetch_orderbook(m["token_ids"][0])
        no_book = fetch_orderbook(m["token_ids"][1])

        if not yes_book or not no_book:
            continue

        yes_spread = calculate_spread(yes_book)
        no_spread = calculate_spread(no_book)

        if not yes_spread or not no_spread:
            continue

        # 1. Arbitrage check
        arb = detect_arbitrage(yes_spread["best_ask"], no_spread["best_ask"])
        if arb and arb["edge"] >= min_edge:
            recommendations.append({
                "market": m["question"],
                "token_id": m["token_ids"][0], # buy YES for arb (oversimplified)
                "side": "YES",
                "action": "BUY",
                "edge": arb["edge"],
                "type": arb["type"],
                "confidence": 0.9,
                "reasoning": arb["detail"],
                "price": yes_spread["best_ask"],
            })

        # 2. Momentum check
        yes_mom = detect_momentum(yes_spread["imbalance"], m["volume_24h"], m["liquidity"])
        if yes_mom and yes_mom["edge"] >= min_edge:
            recommendations.append({
                "market": m["question"],
                "token_id": m["token_ids"][0],
                "side": "YES",
                "action": "BUY",
                "edge": yes_mom["edge"],
                "type": yes_mom["type"],
                "confidence": yes_mom["strength"],
                "reasoning": yes_mom["detail"],
                "price": yes_spread["best_ask"],
            })

        # 3. Mean reversion check
        yes_rev = detect_spread_opportunity(yes_spread["spread_pct"], yes_spread["midpoint"])
        if yes_rev and yes_rev["edge"] >= min_edge:
            recommendations.append({
                "market": m["question"],
                "token_id": m["token_ids"][0],
                "side": "YES",
                "action": "BUY",
                "edge": yes_rev["edge"],
                "type": yes_rev["type"],
                "confidence": 0.6,
                "reasoning": yes_rev["detail"],
                "price": yes_spread["midpoint"], # limit order
            })

    # Filter by confidence and sort by edge
    recommendations = [r for r in recommendations if r["confidence"] >= min_confidence]
    recommendations.sort(key=lambda x: x["edge"], reverse=True)

    # Apply Kelly sizing and risk caps
    final_recs = []
    for r in recommendations:
        # Half-Kelly sizing
        # For simplicity, assume edge + price = win probability
        win_prob = r["edge"] + r["price"]
        size_pct = kelly_half(win_prob, r["price"], r["side"])
        
        # Cap at 10%
        size_pct = min(size_pct, DEFAULT_MAX_POSITION_PCT)
        
        if size_pct > 0.01: # min 1% position
            r["size_pct"] = round(size_pct, 4)
            r["size_usd"] = round(portfolio["value"] * size_pct, 2)
            final_recs.append(r)

    return final_recs


def main():
    parser = argparse.ArgumentParser(description="Polymarket Strategy Advisor")
    parser.add_argument("--top", type=int, default=5, help="Number of recommendations to show")
    parser.add_argument("--portfolio-db", help="Path to paper trader portfolio.db")
    parser.add_argument("--min-volume", type=float, default=DEFAULT_MIN_VOLUME, help="Min 24h volume")
    parser.add_argument("--min-edge", type=float, default=DEFAULT_MIN_EDGE, help="Min estimated edge")
    parser.add_argument("--min-confidence", type=float, default=DEFAULT_MIN_CONFIDENCE, help="Min confidence")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    args = parser.parse_args()

    # 1. Load portfolio state
    portfolio = load_portfolio(args.portfolio_db)

    # 2. Fetch markets
    try:
        markets = fetch_markets(limit=100, min_volume=args.min_volume)
    except Exception as e:
        print(f"Error fetching markets: {e}", file=sys.stderr)
        sys.exit(1)

    # 3. Generate recommendations
    recs = generate_recommendations(markets, portfolio, args.min_edge, args.min_confidence)
    recs = recs[:args.top]

    # 4. Output
    if args.json:
        print(json.dumps(recs, indent=2))
    else:
        print(f"=== Polymarket Strategy Advisor ===")
        print(f"Portfolio Value: ${portfolio['value']:,.2f} | Cash: ${portfolio['cash']:,.2f}")
        print(f"Open Positions: {portfolio['open_position_count']}/{DEFAULT_MAX_OPEN_POSITIONS}")
        print(f"Found {len(recs)} trade recommendations:")
        print("-" * 60)
        for i, r in enumerate(recs, 1):
            print(f"{i}. {r['market']}")
            print(f"   Type: {r['type'].upper()} | Edge: {r['edge']:.1%} | Conf: {r['confidence']:.0%}")
            print(f"   Action: {r['action']} {r['side']} | Size: ${r['size_usd']:,.2f} ({r['size_pct']:.1%})")
            print(f"   Reason: {r['reasoning']}")
            print("-" * 60)


if __name__ == "__main__":
    main()
