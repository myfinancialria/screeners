"""
Fetch historical OHLCV from FYERS into a clean pandas DataFrame.

get_history("NSE:SBIN-EQ", resolution="D", days=300) -> DataFrame indexed by
datetime with columns: open, high, low, close, volume.

Handles FYERS per-request range limits by fetching in chunks and concatenating.
"""
import datetime as dt
import time

import pandas as pd

from fyers_data import get_fyers
from ratelimit import limiter


def _history_call(fyers, params, retries=3):
    """Rate-limited history call with backoff on transient/rate-limit errors."""
    for attempt in range(retries):
        limiter.wait()
        resp = fyers.history(params)
        if resp.get("s") == "ok":
            return resp
        msg = str(resp).lower()
        transient = any(k in msg for k in ("rate", "limit", "-429", "timeout", "try again"))
        if transient and attempt < retries - 1:
            time.sleep(1.5 * (attempt + 1))
            continue
        return resp
    return resp

# FYERS daily limit is generous; intraday is capped ~100 days/request.
_CHUNK_DAYS = {"D": 360, "1D": 360}
_DEFAULT_INTRADAY_CHUNK = 90


def _chunk_days(resolution: str) -> int:
    return _CHUNK_DAYS.get(resolution.upper(), _DEFAULT_INTRADAY_CHUNK)


def get_history(symbol: str, resolution: str = "D", days: int = 300) -> pd.DataFrame:
    """Return OHLCV DataFrame for `symbol` over the last `days` calendar days."""
    fyers = get_fyers()
    end = dt.date.today()
    start = end - dt.timedelta(days=days)
    step = _chunk_days(resolution)

    candles = []
    cur = start
    while cur <= end:
        chunk_end = min(cur + dt.timedelta(days=step), end)
        resp = _history_call(fyers, {
            "symbol": symbol,
            "resolution": resolution,
            "date_format": "1",
            "range_from": str(cur),
            "range_to": str(chunk_end),
            "cont_flag": "1",
        })
        if resp.get("s") == "ok":
            candles.extend(resp.get("candles", []))
        elif "invalid symbol" in str(resp).lower():
            raise RuntimeError(f"invalid symbol {symbol}")
        cur = chunk_end + dt.timedelta(days=1)

    if not candles:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])

    df = pd.DataFrame(candles, columns=["ts", "open", "high", "low", "close", "volume"])
    df = df.drop_duplicates(subset="ts").sort_values("ts")
    df["datetime"] = pd.to_datetime(df["ts"], unit="s", utc=True).dt.tz_convert("Asia/Kolkata")
    df = df.set_index("datetime").drop(columns="ts")
    return df.astype(float)
