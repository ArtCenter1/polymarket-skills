# Findings: Issue #3 — Windows Compatibility Path Adjustments

## Initial Document Scan (README.md)

### venv Activation
- **Line 54**: `source ~/.venv/bin/activate` — Linux/Mac only
- **Missing**: Windows alternative (`.venv/Scripts/activate` or `.venv\Scripts\activate`)

### Setup Commands
- **Lines 233-238**: `source .env` — bash-ism, does not work on Windows CMD/PowerShell
  - Windows alternative: `.env.ps1` or sourcing through bash

### Proxy/Network Section
- **Lines 280-289**: Uses `export` bash syntax for env vars
  - Windows CMD: `set HTTPS_PROXY=http://127.0.0.1:7890`
  - Windows PS: `$env:HTTPS_PROXY="http://127.0.0.1:7890"`

### Data Storage Section
- **Lines 307-309**: Uses `~/.polymarket-paper/` paths — this is standard and works cross-platform with pathlib

## Initial Document Scan (WEB_FRONTEND_README.md)
- **Lines 59-62**: Already shows both Linux/Mac and Windows venv activation — GOOD
- **Lines 165-169**: Already shows Linux/Mac (`lsof`) and Windows (`netstat`) port commands — GOOD
- Rest of file is generally cross-platform compatible

## Initial Document Scan (CLAUDE.md)
- **Line 97**: `source ~/.venv/bin/activate` — Linux/Mac only
- **Line 232**: `source ~/.venv/bin/activate` — Linux/Mac only
- Both need Windows alternatives documented

## Code Scan Plan
- Search for `os.path.join` vs `Path.home()` usage
- Search for hardcoded `/` separators
- Search for posix-only path assumptions
- Check all SQLite DB path constructions
