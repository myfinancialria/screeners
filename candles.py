"""
Vectorized candlestick pattern detection.

Each detector returns a boolean Series aligned to df. detect_patterns(df)
adds one boolean column per pattern and a 'bull_pattern'/'bear_pattern'
summary column listing the patterns firing on each bar.

Patterns are intentionally *shape* detectors — confirmation (trend, volume,
S/R proximity) is layered on top in signals.py, as patterns alone are noisy.
"""
import numpy as np
import pandas as pd

BULLISH = ["bullish_engulfing", "hammer", "morning_star", "bullish_marubozu", "piercing"]
BEARISH = ["bearish_engulfing", "shooting_star", "evening_star", "bearish_marubozu", "dark_cloud"]
NEUTRAL = ["doji", "inside_bar"]


def _parts(df):
    o, h, l, c = df["open"], df["high"], df["low"], df["close"]
    body = (c - o).abs()
    rng = (h - l).replace(0, np.nan)
    upper = h - np.maximum(o, c)
    lower = np.minimum(o, c) - l
    return o, h, l, c, body, rng, upper, lower


def detect_patterns(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    o, h, l, c, body, rng, upper, lower = _parts(out)
    avg_body = body.rolling(14).mean()
    bull = c > o
    bear = c < o
    po, pc = o.shift(1), c.shift(1)
    pbody = (pc - po).abs()

    # --- single-candle ---
    out["doji"] = body <= 0.1 * rng

    out["hammer"] = (lower >= 2 * body) & (upper <= body) & (body > 0)

    out["shooting_star"] = (upper >= 2 * body) & (lower <= body) & (body > 0)

    out["bullish_marubozu"] = bull & (body >= 0.9 * rng) & (body > avg_body)
    out["bearish_marubozu"] = bear & (body >= 0.9 * rng) & (body > avg_body)

    # --- two-candle ---
    out["bullish_engulfing"] = (
        (pc < po) & bull & (o <= pc) & (c >= po) & (body > pbody)
    )
    out["bearish_engulfing"] = (
        (pc > po) & bear & (o >= pc) & (c <= po) & (body > pbody)
    )
    # piercing / dark cloud (gap then close past prior midpoint)
    pmid = (po + pc) / 2
    out["piercing"] = (pc < po) & bull & (o < l.shift(1)) & (c > pmid) & (c < po)
    out["dark_cloud"] = (pc > po) & bear & (o > h.shift(1)) & (c < pmid) & (c > po)

    # inside bar (current range inside previous range)
    out["inside_bar"] = (h < h.shift(1)) & (l > l.shift(1))

    # --- three-candle stars ---
    o2, c2 = o.shift(2), c.shift(2)
    body1 = (c.shift(1) - o.shift(1)).abs()
    small_mid = body1 <= 0.5 * (c2 - o2).abs()
    out["morning_star"] = (
        (c2 < o2) & small_mid & bull & (c > (o2 + c2) / 2)
    )
    out["evening_star"] = (
        (c2 > o2) & small_mid & bear & (c < (o2 + c2) / 2)
    )

    pattern_cols = BULLISH + BEARISH + NEUTRAL
    out[pattern_cols] = out[pattern_cols].fillna(False)

    def names(row, cols):
        return ",".join(p for p in cols if row.get(p))

    out["bull_pattern"] = out.apply(lambda r: names(r, BULLISH), axis=1)
    out["bear_pattern"] = out.apply(lambda r: names(r, BEARISH), axis=1)
    return out
