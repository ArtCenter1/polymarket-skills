# Findings: Code Structure Research & UI Design Ideas

## Code Structure Research

### web_frontend.py (193 lines)

**Key observations:**
1. **`run_command(cmd)`** (line 30-47) — uses `subprocess.run(cmd, shell=True)` with a string command. Returns parsed JSON.
2. **All callers pass string commands** built via f-strings:
   - `update_portfolio()` (line 49-65): Paper engine portfolio/trades
   - `update_markets()` (line 67-83): Scan markets + find edges
   - `update_monitor()` (line 85-106): Get prices
   - `execute_trade()` (line 157-171): Execute paper trade (POST)
   - `health_check()` (line 173-181): Health check
3. **Environment**: Currently passes `env=os.environ`, so child processes inherit proxy/SSL vars if they're set in the parent's environment. But there's no persistence mechanism to manage these settings.
4. **Only Flask import**: Flask, subprocess, sys, sqlite3, threading, time, datetime

### templates/dashboard.html (364 lines)

**Key observations:**
1. Tailwind CSS + Chart.js for visuals
2. Standard dashboard sections: Status Overview, Charts, Markets, Opportunities
3. No settings page or modal exists
4. UI pattern: cards, buttons, loading states

### polymarket_common/connectivity.py (204 lines)

**Key observations:**
1. Reads `HTTP_PROXY`, `HTTPS_PROXY`, `POLYMARKET_SSL_VERIFY` from env vars
2. Patches urllib, requests, and py_clob_client at import time
3. Public helpers: `get_proxy_url()`, `get_ssl_verify()`, `get_requests_proxies()`, `get_requests_kwargs()`

## UI Design Ideas for Settings Modal

1. **Settings button** in header area, next to Refresh button
2. **Modal overlay** with form fields:
   - HTTP Proxy URL (text input, placeholder: `http://127.0.0.1:7890`)
   - HTTPS Proxy URL (text input, placeholder: `https://127.0.0.1:7890`)
   - SSL Verify (toggle/checkbox)
   - Save/Cancel buttons
3. **Toast notification** on successful save
4. **Tailwind-styled** to match existing theme (indigo/blue palette)

## Environment Variable Flow

```
config.json → web_frontend.py loads config
                  ↓
           env_copy = os.environ.copy()
           env_copy["HTTP_PROXY"] = config_val
           env_copy["HTTPS_PROXY"] = config_val
           env_copy["POLYMARKET_SSL_VERIFY"] = config_val
                  ↓
           subprocess.run(..., env=env_copy)
                  ↓
           child script imports polymarket_common.connectivity
                  ↓
           connectivity.py reads env vars, patches libraries
```
