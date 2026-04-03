#!/usr/bin/env python3
"""
Polymarket Trading Agent Web Frontend
A beautiful web interface for monitoring and interacting with the Polymarket trading system.
"""

import json
import os
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

# Global variables for caching data
cached_data = {
    'portfolio': {},
    'markets': [],
    'opportunities': [],
    'monitor_data': {},
    'last_update': None
}

def run_command(cmd):
    """Run a command and return the output"""
    try:
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=30)
        if result.returncode == 0:
            return json.loads(result.stdout) if result.stdout.strip() else {}
        else:
            return {"error": result.stderr}
    except Exception as e:
        return {"error": str(e)}

def update_portfolio():
    """Update portfolio data"""
    try:
        # Get portfolio data
        portfolio_cmd = f"{sys.executable} polymarket-paper-trader/scripts/paper_engine.py --action portfolio --json"
        portfolio_data = run_command(portfolio_cmd)
        if "error" not in portfolio_data:
            cached_data['portfolio'] = portfolio_data
        
        # Get trade history
        trades_cmd = f"{sys.executable} polymarket-paper-trader/scripts/paper_engine.py --action trades --json"
        trades_data = run_command(trades_cmd)
        if "error" not in trades_data:
            cached_data['trades'] = trades_data
            
    except Exception as e:
        cached_data['portfolio'] = {"error": str(e)}

def update_markets():
    """Update market data"""
    try:
        # Scan markets
        markets_cmd = f"{sys.executable} polymarket-scanner/scripts/scan_markets.py --limit 20 --min-volume 10000"
        markets_data = run_command(markets_cmd)
        if isinstance(markets_data, list) and "error" not in markets_data[0] if markets_data else False:
            cached_data['markets'] = markets_data
            
        # Find opportunities
        edges_cmd = f"{sys.executable} polymarket-analyzer/scripts/find_edges.py --min-edge 0.01 --limit 50"
        edges_data = run_command(edges_cmd)
        if isinstance(edges_data, list) and "error" not in edges_data[0] if edges_data else False:
            cached_data['opportunities'] = edges_data
            
    except Exception as e:
        cached_data['markets'] = [{"error": str(e)}]

def update_monitor():
    """Update monitoring data"""
    try:
        # Get current prices for a few key tokens if we have markets
        if cached_data['markets'] and len(cached_data['markets']) > 0 and "error" not in cached_data['markets'][0]:
            token_ids = []
            for market in cached_data['markets'][:5]:  # First 5 markets
                if 'token_ids' in market and len(market['token_ids']) >= 2:
                    token_ids.extend(market['token_ids'][:2])
            
                    if token_ids:
                        # Get prices for these tokens
                        price_cmd = f"{sys.executable} polymarket-scanner/scripts/get_prices.py"
                        for token_id in token_ids[:10]:  # Limit to 10 tokens
                            price_cmd += f" --token-id {token_id}"
                        
                        prices_data = run_command(price_cmd)
                        if isinstance(prices_data, list) and len(prices_data) > 0 and "error" not in prices_data[0]:
                            cached_data['monitor_data'] = {item['token_id']: item for item in prices_data}
                    
    except Exception as e:
        cached_data['monitor_data'] = {"error": str(e)}

def background_updater():
    """Background thread to update data periodically"""
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

@app.route('/')
def index():
    """Main dashboard"""
    return render_template('dashboard.html')

@app.route('/api/portfolio')
def api_portfolio():
    """Get portfolio data"""
    return jsonify(cached_data.get('portfolio', {}))

@app.route('/api/markets')
def api_markets():
    """Get market data"""
    return jsonify(cached_data.get('markets', []))

@app.route('/api/opportunities')
def api_opportunities():
    """Get trading opportunities"""
    return jsonify(cached_data.get('opportunities', []))

@app.route('/api/monitor')
def api_monitor():
    """Get monitoring data"""
    return jsonify(cached_data.get('monitor_data', {}))

@app.route('/api/status')
def api_status():
    """Get overall system status"""
    return jsonify({
        'last_update': cached_data.get('last_update'),
        'portfolio_count': len(cached_data.get('portfolio', {})),
        'markets_count': len(cached_data.get('markets', [])),
        'opportunities_count': len(cached_data.get('opportunities', [])),
        'monitor_count': len(cached_data.get('monitor_data', {}))
    })

@app.route('/api/execute_trade', methods=['POST'])
def execute_trade():
    """Execute a trade recommendation"""
    try:
        data = request.get_json()
        recommendation = data.get('recommendation', {})
        
        # Format recommendation for paper trader
        rec_json = json.dumps(recommendation)
        cmd = f"{sys.executable} polymarket-paper-trader/scripts/execute_paper.py --recommendation '{rec_json}'"
        
        result = run_command(cmd)
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/health_check')
def health_check():
    """Run portfolio health check"""
    try:
        cmd = f"{sys.executable} polymarket-paper-trader/scripts/health_check.py --json"
        result = run_command(cmd)
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

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