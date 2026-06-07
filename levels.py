"""
Support/Resistance zones and trendlines from swing pivots.

- swing_pivots(df)      -> (pivot_high_idx, pivot_low_idx) positional indices
- sr_levels(df)         -> [{price, touches, kind}]  horizontal S/R zones
- trendlines(df)        -> {'support':line, 'resistance':line} sloped lines
- market_structure(df)  -> 'uptrend' | 'downtrend' | 'sideways'

A 'line' is (slope, intercept, x_start, x_end) in positional-index space; its
value at bar x is slope*x + intercept.
"""
import numpy as np
import pandas as pd


def swing_pivots(df: pd.DataFrame, left: int = 3, right: int = 3):
    highs, lows = df["high"].values, df["low"].values
    n = len(df)
    ph, pl = [], []
    for i in range(left, n - right):
        win_h = highs[i - left:i + right + 1]
        win_l = lows[i - left:i + right + 1]
        if highs[i] == win_h.max():
            ph.append(i)
        if lows[i] == win_l.min():
            pl.append(i)
    return ph, pl


def sr_levels(df, left=3, right=3, tol_pct=0.6, max_levels=8):
    """Cluster pivot prices into horizontal S/R zones."""
    ph, pl = swing_pivots(df, left, right)
    pts = [(i, df["high"].iloc[i]) for i in ph] + [(i, df["low"].iloc[i]) for i in pl]
    if not pts:
        return []
    pts.sort(key=lambda x: x[1])

    clusters = []
    for idx, price in pts:
        if clusters and abs(price - clusters[-1]["mean"]) <= clusters[-1]["mean"] * tol_pct / 100:
            cl = clusters[-1]
            cl["prices"].append(price)
            cl["last_idx"] = max(cl["last_idx"], idx)
            cl["mean"] = float(np.mean(cl["prices"]))
        else:
            clusters.append({"prices": [price], "mean": float(price), "last_idx": idx})

    last_close = df["close"].iloc[-1]
    levels = []
    for cl in clusters:
        levels.append({
            "price": round(cl["mean"], 2),
            "touches": len(cl["prices"]),
            "last_idx": cl["last_idx"],
            "kind": "support" if cl["mean"] < last_close else "resistance",
        })
    # rank by touches, then recency
    levels.sort(key=lambda x: (x["touches"], x["last_idx"]), reverse=True)
    return levels[:max_levels]


def _fit(idx, prices):
    if len(idx) < 2:
        return None
    idx = np.array(idx[-4:], dtype=float)
    prices = np.array(prices[-4:], dtype=float)
    slope, intercept = np.polyfit(idx, prices, 1)
    return float(slope), float(intercept), int(idx[0]), len(prices) - 1


def trendlines(df, left=3, right=3):
    ph, pl = swing_pivots(df, left, right)
    n = len(df)
    res = _fit(ph, [df["high"].iloc[i] for i in ph])
    sup = _fit(pl, [df["low"].iloc[i] for i in pl])
    out = {}
    if res:
        slope, intercept, x0, _ = res
        out["resistance"] = (slope, intercept, x0, n - 1)
    if sup:
        slope, intercept, x0, _ = sup
        out["support"] = (slope, intercept, x0, n - 1)
    return out


def line_value(line, x):
    slope, intercept, _, _ = line
    return slope * x + intercept


def market_structure(df, left=3, right=3, lookback=6):
    """Classify trend from the sequence of recent swing highs/lows."""
    ph, pl = swing_pivots(df, left, right)
    if len(ph) < 2 or len(pl) < 2:
        return "sideways"
    highs = [df["high"].iloc[i] for i in ph[-lookback:]]
    lows = [df["low"].iloc[i] for i in pl[-lookback:]]
    hh = highs[-1] > highs[-2]
    hl = lows[-1] > lows[-2]
    lh = highs[-1] < highs[-2]
    ll = lows[-1] < lows[-2]
    if hh and hl:
        return "uptrend"
    if lh and ll:
        return "downtrend"
    return "sideways"
