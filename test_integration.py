#!/usr/bin/env python3
"""Integration test: verify connectivity import works from each script's path."""
import os
import sys
import subprocess

REPO = os.path.dirname(os.path.abspath(__file__))
VENV_PYTHON = os.path.join(REPO, ".venv", "Scripts", "python")

scripts = [
    "polymarket-scanner/scripts/scan_markets.py",
    "polymarket-scanner/scripts/get_prices.py",
    "polymarket-scanner/scripts/get_orderbook.py",
    "polymarket-analyzer/scripts/find_edges.py",
    "polymarket-analyzer/scripts/momentum_scanner.py",
    "polymarket-analyzer/scripts/analyze_orderbook.py",
    "polymarket-strategy-advisor/scripts/advisor.py",
    "polymarket-strategy-advisor/scripts/backtest.py",
    "polymarket-paper-trader/scripts/paper_engine.py",
    "polymarket-paper-trader/scripts/health_check.py",
    "polymarket-monitor/scripts/monitor_prices.py",
    "polymarket-monitor/scripts/watch_market.py",
    "polymarket-live-executor/scripts/check_positions.py",
    "polymarket-live-executor/scripts/execute_live.py",
]

ok = 0
fail = 0
for script in scripts:
    # Run each script with --help to verify it imports without error
    result = subprocess.run(
        [VENV_PYTHON, script, "--help"],
        capture_output=True, text=True, timeout=15,
        env={**os.environ, "POLYMARKET_SSL_VERIFY": "false"},
    )
    if result.returncode == 0:
        print(f"OK: {script}")
        ok += 1
    else:
        print(f"FAIL: {script}")
        print(f"  stderr: {result.stderr[:200]}")
        fail += 1

print(f"\n{ok} OK, {fail} FAIL out of {len(scripts)} scripts")
