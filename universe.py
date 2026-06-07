"""Predefined stock universes in FYERS symbol format."""
import csv
import time
import urllib.request
from pathlib import Path

NSE_CM_URL = "https://public.fyers.in/sym_details/NSE_CM.csv"
_CACHE = Path(__file__).with_name(".cache")
_CACHE.mkdir(exist_ok=True)
C_SYMBOL = 9  # symbol-ticker column in the FYERS master


def load_nse_equity(max_age_sec: int = 86400):
    """All NSE cash-market equity (-EQ) symbols from the FYERS master."""
    path = _CACHE / "NSE_CM.csv"
    fresh = path.exists() and (time.time() - path.stat().st_mtime) < max_age_sec
    if not fresh:
        urllib.request.urlretrieve(NSE_CM_URL, path)
    symbols = []
    with path.open(newline="") as f:
        for row in csv.reader(f):
            if len(row) > C_SYMBOL and row[C_SYMBOL].endswith("-EQ"):
                symbols.append(row[C_SYMBOL])
    return sorted(set(symbols))

NIFTY50 = [
    "ADANIENT", "ADANIPORTS", "APOLLOHOSP", "ASIANPAINT", "AXISBANK",
    "BAJAJ-AUTO", "BAJFINANCE", "BAJAJFINSV", "BEL", "BPCL",
    "BHARTIARTL", "BRITANNIA", "CIPLA", "COALINDIA", "DRREDDY",
    "EICHERMOT", "GRASIM", "HCLTECH", "HDFCBANK", "HDFCLIFE",
    "HEROMOTOCO", "HINDALCO", "HINDUNILVR", "ICICIBANK", "ITC",
    "INDUSINDBK", "INFY", "JSWSTEEL", "KOTAKBANK", "LT",
    "M&M", "MARUTI", "NTPC", "NESTLEIND", "ONGC",
    "POWERGRID", "RELIANCE", "SBILIFE", "SBIN", "SHRIRAMFIN",
    "SUNPHARMA", "TCS", "TATACONSUM", "TMPV", "TATASTEEL",
    "TECHM", "TITAN", "TRENT", "ULTRACEMCO", "WIPRO",
]


def fyers_symbols(names, exchange="NSE", suffix="-EQ"):
    return [f"{exchange}:{n}{suffix}" for n in names]


UNIVERSES = {
    "nifty50": fyers_symbols(NIFTY50),
}


def resolve_universe(name):
    key = name.lower()
    if key in ("nse", "nse_all", "all"):
        return load_nse_equity()
    if key in UNIVERSES:
        return UNIVERSES[key]
    raise SystemExit(f"Unknown universe '{name}'. Available: nse_all, {', '.join(UNIVERSES)}")
