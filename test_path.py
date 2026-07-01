import os, sys
f = os.path.abspath("polymarket-scanner/scripts/scan_markets.py")
print(f"__file__ would be: {f}")
print(f"dirname: {os.path.dirname(f)}")
print(f"dirname dirname: {os.path.dirname(os.path.dirname(f))}")
