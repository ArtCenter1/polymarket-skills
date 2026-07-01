#!/usr/bin/env python3
"""
Polymarket Trading Agent Web Frontend
A beautiful web interface for monitoring and interacting with the Polymarket trading system.

Security enhancements (Issue #2):
  - All subprocess calls use list-based arguments with shell=False (no shell injection)
  - Proxy/SSL settings are persisted to config.json and propagated to child processes
  - Settings API endpoints (GET/POST /api/settings)
  - Settings UI in the dashboard
"""

import json
import os
import pathlib
import sqlite3
import threading
import time
from datetime import datetime, timedelta
from flask import Flask, render_template, jsonify, request
import subprocess
import sys
from typing import Dict, List, Any, Optional

app = Flask(__name__)
app.config['SECRET_KEY'] = 'polymarket-trading-agent-2026'

# ---------------------------------------------------------------------------
# Configuration persistence
# ---------------------------------------------------------------------------

# Path to the local config file (sibling to this script, in repo root)
CONFIG_PATH = pathlib.Path(__file__).resolve().parent / "config.json"

DEFAULT_CONFIG: Dict[str, Any] = {
    "HTTP_PROXY": "",
    "HTTPS_PROXY": "",
    "POLYMARKET_SSL_VERIFY": True,
}


def load_config() -> Dict[str, Any]:
    """Load proxy/SSL config from ``config.json``.

    Returns a dict with keys ``HTTP_PROXY``, ``HTTPS_PROXY``,
    ``POLYMARKET_SSL_VERIFY``. Missing or invalid keys fall back to
    defaults.
    """
    if not CONFIG_PATH.exists():
        return dict(DEFAULT_CONFIG)

    try:
        raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return dict(DEFAULT_CONFIG)

    # Merge with defaults to fill any missing keys
    cfg = dict(DEFAULT_CONFIG)
    cfg.update(raw)
    # Coerce SSL verify to bool
    if isinstance(cfg["POLYMARKET_SSL_VERIFY"], str):
        cfg["POLYMARKET_SSL_VERIFY"] = cfg["POLYMARKET_SSL_VERIFY"].strip().lower() != "false"
    cfg["POLYMARKET_SSL_VERIFY"] = bool(cfg["POLYMARKET_SSL_VERIFY"])
    return cfg


def save_config(config: Dict[str, Any]) -> None:
    """Persist proxy/SSL config to ``config.json``.

    Args:
        config: Dict with keys ``HTTP_PROXY``, ``HTTPS_PROXY``,
            ``POLYMARKET_SSL_VERIFY``.
    """
    # Build a clean dict for serialisation
    out = {
        "HTTP_PROXY": str(config.get("HTTP_PROXY", "")),
        "HTTPS_PROXY": str(config.get("HTTPS_PROXY", "")),
        "POLYMARKET_SSL_VERIFY": bool(config.get("POLYMARKET_SSL_VERIFY", True)),
    }
    CONFIG_PATH.write_text(
        json.dumps(out, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def build_subprocess_env() -> Dict[str, str]:
    """Return a copy of the current environment enriched with proxy/SSL vars.

    Reads the persisted config (or defaults) and injects ``HTTP_PROXY``,
    ``HTTPS_PROXY``, and ``POLYMARKET_SSL_VERIFY`` into the environment
    so that every child subprocess inherits them. Child scripts that import
    ``polymarket_common.connectivity`` automatically pick up these vars.
    """
    env = os.environ.copy()
    cfg = load_config()

    proxy_http = cfg.get("HTTP_PROXY", "").strip()
    proxy_https = cfg.get("HTTPS_PROXY", "").strip()
    ssl_verify = cfg.get("POLYMARKET_SSL_VERIFY", True)

    if proxy_http:
        env["HTTP_PROXY"] = proxy_http
    if proxy_https:
        env["HTTPS_PROXY"] = proxy_https
    env["POLYMARKET_SSL_VERIFY"] = str(ssl_verify).lower()

    return env


# ---------------------------------------------------------------------------
# Subprocess execution (safe, list-based, no shell=True)
# ---------------------------------------------------------------------------


def run_command(args: List[str]) -> Any:
    """Run a command (list-based) and return parsed JSON output.

    All subprocess calls go through this function to guarantee:
      - ``shell=False`` (no shell injection)
      - Proxy/SSL env vars are inherited from persistant config
      - Consistent timeout and error handling

    Args:
        args: List of arguments, e.g. ``[sys.executable, "script.py", "--flag", "val"]``

    Returns:
        Parsed JSON (dict or list), or ``{"error": ...}`` on failure.
    """
    try:
        env = build_subprocess_env()
        result = subprocess.run(
            args,
            shell=False,
            capture_output=True,
            text=True,
            timeout=30,
            env=env,
        )
        if result.returncode == 0:
            stdout = result.stdout.strip()
            return json.loads(stdout) if stdout else {}
        else:
            return {"error": result.stderr.strip() or f"Exit code {result.returncode}"}
    except json.JSONDecodeError:
        return {"error": "Non-JSON output from subprocess"}
    except subprocess.TimeoutExpired:
        return {"error": "Command timed out after 30s"}
    except Exception as e:
        return {"error": str(e)}


def python_script_args(script_rel_path: str, *extra: str) -> List[str]:
    """Build a safe list-based argument vector for running a Python script.

    Args:
        script_rel_path: Relative path from repo root to the script.
        *extra: Additional CLI arguments (each as a separate string).

    Returns:
        List suitable for passing to ``run_command()``.
    """
    return [sys.executable, script_rel_path, *extra]


# ---------------------------------------------------------------------------
# Helpers for command construction (keeps call sites DRY)
# ---------------------------------------------------------------------------

REPO_ROOT = pathlib.Path(__file__).resolve().parent


def _script_path(rel: str) -> str:
    """Return absolute path to a script under the repo root."""
    return str(REPO_ROOT / rel)


# ---------------------------------------------------------------------------
# Data update functions
# ---------------------------------------------------------------------------

# Global variables for caching data
cached_data: Dict[str, Any] = {
    'portfolio': {},
    'markets': [],
    'opportunities': [],
    'monitor_data': {},
    'last_update': None,
}


def update_portfolio() -> None:
    """Update portfolio data from paper-trader engine."""
    try:
        # Get portfolio data
        portfolio_data = run_command(
            python_script_args(
                _script_path("polymarket-paper-trader/scripts/paper_engine.py"),
                "--action", "portfolio", "--json",
            )
        )
        if "error" not in portfolio_data:
            cached_data['portfolio'] = portfolio_data

        # Get trade history
        trades_data = run_command(
            python_script_args(
                _script_path("polymarket-paper-trader/scripts/paper_engine.py"),
                "--action", "trades", "--json",
            )
        )
        if "error" not in trades_data:
            cached_data['trades'] = trades_data

    except Exception as e:
        cached_data['portfolio'] = {"error": str(e)}


def update_markets() -> None:
    """Update market data from scanner & analyzer."""
    try:
        # Scan markets
        markets_data = run_command(
            python_script_args(
                _script_path("polymarket-scanner/scripts/scan_markets.py"),
                "--limit", "20", "--min-volume", "10000",
            )
        )
        if isinstance(markets_data, list) and markets_data and "error" not in markets_data[0]:
            cached_data['markets'] = markets_data

        # Find opportunities
        edges_data = run_command(
            python_script_args(
                _script_path("polymarket-analyzer/scripts/find_edges.py"),
                "--min-edge", "0.01", "--limit", "50",
            )
        )
        if isinstance(edges_data, list) and edges_data and "error" not in edges_data[0]:
            cached_data['opportunities'] = edges_data

    except Exception as e:
        cached_data['markets'] = [{"error": str(e)}]


def update_monitor() -> None:
    """Update monitoring data for key tokens."""
    try:
        # Get current prices for a few key tokens if we have markets
        if cached_data['markets'] and len(cached_data['markets']) > 0 and "error" not in cached_data['markets'][0]:
            token_ids = []
            for market in cached_data['markets'][:5]:  # First 5 markets
                if 'token_ids' in market and len(market['token_ids']) >= 2:
                    token_ids.extend(market['token_ids'][:2])

            if token_ids:
                # Build price command with token IDs
                price_args = python_script_args(
                    _script_path("polymarket-scanner/scripts/get_prices.py"),
                )
                for token_id in token_ids[:10]:  # Limit to 10 tokens
                    price_args.extend(["--token-id", str(token_id)])

                prices_data = run_command(price_args)
                if isinstance(prices_data, list) and len(prices_data) > 0 and "error" not in prices_data[0]:
                    cached_data['monitor_data'] = {item['token_id']: item for item in prices_data}

    except Exception as e:
        cached_data['monitor_data'] = {"error": str(e)}


def background_updater() -> None:
    """Background thread to update data periodically."""
    while True:
        try:
            update_portfolio()
            update_markets()
            update_monitor()
            cached_data['last_update'] = datetime.now().isoformat()
            time.sleep(30)  # Update every 30 seconds
        except Exception as e:
            print(f"Background update error: {e}")
            time.sleep(5)


# ---------------------------------------------------------------------------
# Web routes
# ---------------------------------------------------------------------------


@app.route('/')
def index():
    """Main dashboard."""
    return render_template('dashboard.html')


@app.route('/api/portfolio')
def api_portfolio():
    """Get portfolio data."""
    return jsonify(cached_data.get('portfolio', {}))


@app.route('/api/markets')
def api_markets():
    """Get market data."""
    return jsonify(cached_data.get('markets', []))


@app.route('/api/opportunities')
def api_opportunities():
    """Get trading opportunities."""
    return jsonify(cached_data.get('opportunities', []))


@app.route('/api/monitor')
def api_monitor():
    """Get monitoring data."""
    return jsonify(cached_data.get('monitor_data', {}))


@app.route('/api/status')
def api_status():
    """Get overall system status."""
    return jsonify({
        'last_update': cached_data.get('last_update'),
        'portfolio_count': len(cached_data.get('portfolio', {})),
        'markets_count': len(cached_data.get('markets', [])),
        'opportunities_count': len(cached_data.get('opportunities', [])),
        'monitor_count': len(cached_data.get('monitor_data', {})),
    })


@app.route('/api/settings', methods=['GET'])
def api_get_settings():
    """Get current proxy/SSL settings.

    Returns the persisted configuration (or defaults if none saved).
    """
    return jsonify(load_config())


@app.route('/api/settings', methods=['POST'])
def api_save_settings():
    """Save proxy/SSL settings.

    Expects JSON body with optional keys:
      - ``HTTP_PROXY`` (string)
      - ``HTTPS_PROXY`` (string)
      - ``POLYMARKET_SSL_VERIFY`` (bool)

    Settings are persisted to ``config.json`` and will be inherited by
    all subsequent subprocess calls.
    """
    try:
        data = request.get_json(force=True)
    except Exception:
        return jsonify({"error": "Invalid JSON body"}), 400

    if not isinstance(data, dict):
        return jsonify({"error": "Body must be a JSON object"}), 400

    # Merge with existing config so partial updates don't clobber other fields
    current = load_config()
    for key in ("HTTP_PROXY", "HTTPS_PROXY", "POLYMARKET_SSL_VERIFY"):
        if key in data:
            current[key] = data[key]

    save_config(current)
    return jsonify({"status": "ok", "settings": load_config()})


@app.route('/api/execute_trade', methods=['POST'])
def execute_trade():
    """Execute a trade recommendation via the paper trader.

    Expects JSON body with a ``recommendation`` dict.
    """
    try:
        data = request.get_json()
        recommendation = data.get('recommendation', {})

        # Pass recommendation as a JSON string argument
        rec_json = json.dumps(recommendation)
        cmd_args = python_script_args(
            _script_path("polymarket-paper-trader/scripts/execute_paper.py"),
            "--recommendation", rec_json,
        )
        result = run_command(cmd_args)
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/health_check')
def health_check():
    """Run portfolio health check script."""
    try:
        cmd_args = python_script_args(
            _script_path("polymarket-paper-trader/scripts/health_check.py"),
            "--json",
        )
        result = run_command(cmd_args)
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

if __name__ == '__main__':
    # Start background updater thread
    updater_thread = threading.Thread(target=background_updater, daemon=True)
    updater_thread.start()

    # Initial data load
    update_portfolio()
    update_markets()
    update_monitor()

    # Run the Flask app
    app.run(host='0.0.0.0', port=5000, debug=False, threaded=True)
