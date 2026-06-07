"""
Live prices and F&O symbol resolution via Fyers.

- ltp(symbols)            -> {symbol: last_price}
- resolve_fo(...)         -> (fyers_symbol, lot_size, expiry_str) for the NEAREST expiry
- The F&O symbol master is downloaded from Fyers and cached locally for a day.
"""
import csv
import time
import urllib.request
from pathlib import Path

from fyers_apiv3 import fyersModel

from envtools import load_env

# Fyers public symbol masters
FO_MASTER_URL = "https://public.fyers.in/sym_details/NSE_FO.csv"
BSE_FO_MASTER_URL = "https://public.fyers.in/sym_details/BSE_FO.csv"
MASTER_DIR = Path(__file__).with_name(".cache")
MASTER_DIR.mkdir(exist_ok=True)

# Column indices in the Fyers symbol-master CSV
C_LOT = 3
C_EXPIRY = 8        # epoch seconds
C_SYMBOL = 9        # e.g. NSE:NIFTY2660918100CE
C_UNDERLYING = 13   # e.g. NIFTY
C_STRIKE = 15       # float; -1.0 for futures
C_OPTTYPE = 16      # CE / PE / XX(future)

_fyers = None


def get_fyers() -> fyersModel.FyersModel:
    """Build a FyersModel from .env (cached)."""
    global _fyers
    if _fyers is None:
        env = load_env()
        token = env.get("FYERS_ACCESS_TOKEN", "").strip()
        if not token:
            raise SystemExit("No access token. Run:  python3 auth.py")
        _fyers = fyersModel.FyersModel(
            client_id=env["FYERS_APP_ID"], token=token, is_async=False
        )
    return _fyers


def ltp(symbols) -> dict:
    """Return {symbol: last_price} for one symbol or a list of them."""
    if isinstance(symbols, str):
        symbols = [symbols]
    if not symbols:
        return {}
    fyers = get_fyers()
    resp = fyers.quotes({"symbols": ",".join(symbols)})
    if resp.get("s") != "ok":
        raise RuntimeError(f"Quote failed: {resp}")
    out = {}
    for d in resp.get("d", []):
        out[d.get("n")] = d.get("v", {}).get("lp")
    return out


def _master_path(name: str) -> Path:
    return MASTER_DIR / name


def _ensure_master(url: str, name: str, max_age_sec: int = 86400) -> Path:
    """Download the symbol master if missing or older than a day."""
    path = _master_path(name)
    fresh = path.exists() and (time.time() - path.stat().st_mtime) < max_age_sec
    if not fresh:
        urllib.request.urlretrieve(url, path)
    return path


def _load_rows(underlying: str, exchange: str = "NSE"):
    """Yield master rows for a given underlying symbol."""
    url, name = (FO_MASTER_URL, "NSE_FO.csv")
    if exchange.upper() == "BSE":
        url, name = (BSE_FO_MASTER_URL, "BSE_FO.csv")
    path = _ensure_master(url, name)
    with path.open(newline="") as f:
        for row in csv.reader(f):
            if len(row) <= C_OPTTYPE:
                continue
            if row[C_UNDERLYING].strip().upper() == underlying.upper():
                yield row


def resolve_fo(underlying: str, kind: str, strike=None, exchange: str = "NSE"):
    """
    Resolve the NEAREST-expiry F&O contract.

    underlying : e.g. "NIFTY", "BANKNIFTY", "RELIANCE"
    kind       : "FUT" | "CE" | "PE"
    strike     : required for CE/PE. Pass "ATM" to pick the strike nearest to spot,
                 or a number. Ignored for FUT.

    Returns (fyers_symbol, lot_size, expiry_str "DD-MMM-YYYY").
    """
    kind = kind.upper()
    opt_target = "XX" if kind == "FUT" else kind
    now = time.time()

    rows = [r for r in _load_rows(underlying, exchange)
            if r[C_OPTTYPE].strip().upper() == opt_target
            and float(r[C_EXPIRY]) >= now - 86400]  # keep today's expiry too
    if not rows:
        raise SystemExit(f"No {kind} contracts found for {underlying} on {exchange}.")

    nearest_exp = min(float(r[C_EXPIRY]) for r in rows)
    rows = [r for r in rows if float(r[C_EXPIRY]) == nearest_exp]

    if kind == "FUT":
        chosen = rows[0]
    else:
        if strike is None:
            raise SystemExit(f"{kind} needs a strike (a number or 'ATM').")
        if str(strike).upper() == "ATM":
            # spot via the underlying's index/eq symbol guess; use future LTP as proxy
            fut_sym, _, _ = resolve_fo(underlying, "FUT", exchange=exchange)
            spot = ltp(fut_sym).get(fut_sym)
            strike_val = min(float(r[C_STRIKE]) for r in rows)  # fallback
            if spot:
                strike_val = min(rows, key=lambda r: abs(float(r[C_STRIKE]) - spot))
                strike_val = float(strike_val[C_STRIKE])
        else:
            strike_val = float(strike)
        matches = [r for r in rows if float(r[C_STRIKE]) == strike_val]
        if not matches:
            avail = sorted({float(r[C_STRIKE]) for r in rows})
            raise SystemExit(
                f"Strike {strike_val} not found. Nearest available: "
                f"{min(avail, key=lambda s: abs(s - strike_val))}"
            )
        chosen = matches[0]

    expiry_str = time.strftime("%d-%b-%Y", time.localtime(float(chosen[C_EXPIRY])))
    return chosen[C_SYMBOL], int(chosen[C_LOT]), expiry_str
