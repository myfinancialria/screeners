"""
Confirmation engine: combine candlestick patterns + S/R + trendlines + volume
+ trend + momentum into a single BULLISH / BEARISH / NEUTRAL call with a score
and a concrete trade plan (entry / stop / targets / R:R).

Patterns alone are noisy — a pattern only counts when the context confirms it
(bullish pattern near support in an uptrend with a volume push, and the mirror
for bearish). This is the heart of the scanner.
"""
import numpy as np
import pandas as pd

from candles import detect_patterns, BULLISH, BEARISH
from indicators import add_indicators
from levels import sr_levels, trendlines, market_structure, line_value


def _nearest(levels, price, kind):
    cand = [L for L in levels if L["kind"] == kind]
    if not cand:
        return None
    if kind == "support":
        below = [L for L in cand if L["price"] <= price]
        return max(below, key=lambda L: L["price"]) if below else None
    above = [L for L in cand if L["price"] >= price]
    return min(above, key=lambda L: L["price"]) if above else None


def analyze_df(df: pd.DataFrame, symbol: str) -> dict:
    if len(df) < 50:
        return {"symbol": symbol, "ok": False, "reason": "not enough data"}

    df = add_indicators(df)
    df = detect_patterns(df)
    last = df.iloc[-1]
    prev = df.iloc[-2]
    n = len(df)
    close = last["close"]
    atr = last["atr"] if not np.isnan(last["atr"]) else close * 0.02
    prox = max(atr * 0.6, close * 0.015)   # "near a level" tolerance

    levels = sr_levels(df)
    tls = trendlines(df)
    structure = market_structure(df)

    near_sup = _nearest(levels, close, "support")
    near_res = _nearest(levels, close, "resistance")
    at_support = near_sup and abs(close - near_sup["price"]) <= prox
    at_resistance = near_res and abs(close - near_res["price"]) <= prox

    bull_patterns = [p for p in BULLISH if last.get(p)]
    bear_patterns = [p for p in BEARISH if last.get(p)]

    candle_pos = (close - last["low"]) / max(last["high"] - last["low"], 1e-9)
    one_bar_move = abs(close - prev["close"]) / prev["close"] * 100
    vol_ratio = last["vol_ratio"] if not np.isnan(last["vol_ratio"]) else 0
    rsi = last["rsi"]
    above200 = not np.isnan(last["ema200"]) and close > last["ema200"]

    # ---------------- BULLISH score ----------------
    bull, bconf = 0, []
    if bull_patterns:
        bull += 20; bconf.append("bullish candle: " + ",".join(bull_patterns))
    if at_support:
        bull += 15; bconf.append(f"at support {near_sup['price']} (x{near_sup['touches']})")
    if close > last["resistance_20"]:
        bull += 25; bconf.append("breakout > 20-bar high")
    if vol_ratio >= 1.5:
        bull += 20; bconf.append(f"volume {vol_ratio:.1f}x avg")
    if close > last["ema20"] > last["ema50"]:
        bull += 15; bconf.append("EMA20>EMA50 uptrend")
    if 55 <= rsi <= 75:
        bull += 10; bconf.append(f"RSI {rsi:.0f}")
    if last["adx"] >= 20:
        bull += 10; bconf.append(f"ADX {last['adx']:.0f}")
    if structure == "uptrend":
        bull += 10; bconf.append("higher highs/lows")
    if candle_pos >= 0.7:
        bull += 5; bconf.append("closes top of range")
    if above200:
        bull += 10; bconf.append("above 200 EMA")
    if not above200 and not np.isnan(last["ema200"]):
        bull -= 20; bconf.append("below 200 EMA (-)")
    if vol_ratio and vol_ratio < 0.8:
        bull -= 15; bconf.append("weak volume (-)")
    if one_bar_move > 8:
        bull -= 15; bconf.append("over-extended bar (-)")

    # ---------------- BEARISH score ----------------
    bear, sconf = 0, []
    if bear_patterns:
        bear += 20; sconf.append("bearish candle: " + ",".join(bear_patterns))
    if at_resistance:
        bear += 15; sconf.append(f"at resistance {near_res['price']} (x{near_res['touches']})")
    if close < last["support_20"]:
        bear += 25; sconf.append("breakdown < 20-bar low")
    if vol_ratio >= 1.5:
        bear += 20; sconf.append(f"volume {vol_ratio:.1f}x avg")
    if close < last["ema20"] < last["ema50"]:
        bear += 15; sconf.append("EMA20<EMA50 downtrend")
    if 25 <= rsi <= 45:
        bear += 10; sconf.append(f"RSI {rsi:.0f}")
    if last["adx"] >= 20:
        bear += 10; sconf.append(f"ADX {last['adx']:.0f}")
    if structure == "downtrend":
        bear += 10; sconf.append("lower highs/lows")
    if candle_pos <= 0.3:
        bear += 5; sconf.append("closes bottom of range")
    if not above200 and not np.isnan(last["ema200"]):
        bear += 10; sconf.append("below 200 EMA")
    if above200:
        bear -= 20; sconf.append("above 200 EMA (-)")

    # ---------------- early bullish setup classification ----------------
    # Catch stocks BEFORE/AS they break out, not just confirmed movers.
    high20 = df["high"].iloc[-20:].max()
    dist_to_high = (high20 - close) / close * 100          # >=0 => still below 20-bar high
    atrp = df["atr_pct"]
    squeeze = bool(len(df) >= 50 and atrp.iloc[-1] < atrp.iloc[-50:].median() * 0.85)
    rng7 = (df["high"].iloc[-7:].max() - df["low"].iloc[-7:].min()) / close * 100
    up = close > last["ema20"] > last["ema50"]
    near_ema20 = abs(close - last["ema20"]) / close * 100 <= 2.5
    avg_value_cr = float((df["close"] * df["volume"]).iloc[-20:].mean() / 1e7)

    if close > last["resistance_20"] and vol_ratio >= 1.3:
        bull_setup = "BREAKOUT"          # breaking out right now
    elif up and 0 <= dist_to_high <= 3 and (squeeze or rng7 <= 8) and 50 <= rsi <= 72:
        bull_setup = "PRE_BREAKOUT"      # coiling just under highs -> likely soon
    elif up and near_ema20 and structure != "downtrend" and (bull_patterns or rsi >= 45):
        bull_setup = "PULLBACK"          # uptrend pullback to support -> continuation
    elif up:
        bull_setup = "TREND"
    else:
        bull_setup = ""
    early_bullish = bull_setup in ("BREAKOUT", "PRE_BREAKOUT", "PULLBACK")

    # ---------------- decide ----------------
    THRESH = 50
    if bull >= bear and bull >= THRESH:
        direction, score, conf = "BULLISH", bull, bconf
    elif bear > bull and bear >= THRESH:
        direction, score, conf = "BEARISH", bear, sconf
    else:
        direction, score, conf = "NEUTRAL", max(bull, bear), (bconf if bull >= bear else sconf)

    # ---------------- trade plan ----------------
    plan = {}
    if direction == "BULLISH":
        entry = round(last["high"] + 0.05, 2)
        stop = round(min(last["low"], near_sup["price"]) if near_sup else last["low"], 2)
        risk = max(entry - stop, 0.01)
        plan = {
            "entry": entry, "stop_loss": stop,
            "target1": round(entry + 1.5 * risk, 2),
            "target2": round(near_res["price"], 2) if near_res else round(entry + 2 * risk, 2),
            "risk_per_share": round(risk, 2),
            "risk_reward": round((near_res["price"] - entry) / risk, 2) if near_res else 2.0,
        }
    elif direction == "BEARISH":
        entry = round(last["low"] - 0.05, 2)
        stop = round(max(last["high"], near_res["price"]) if near_res else last["high"], 2)
        risk = max(stop - entry, 0.01)
        plan = {
            "entry": entry, "stop_loss": stop,
            "target1": round(entry - 1.5 * risk, 2),
            "target2": round(near_sup["price"], 2) if near_sup else round(entry - 2 * risk, 2),
            "risk_per_share": round(risk, 2),
            "risk_reward": round((entry - near_sup["price"]) / risk, 2) if near_sup else 2.0,
        }

    return {
        "symbol": symbol, "ok": True,
        "direction": direction, "score": int(score),
        "bull_setup": bull_setup, "early_bullish": early_bullish,
        "dist_to_high_pct": round(dist_to_high, 2), "squeeze": squeeze,
        "avg_value_cr": round(avg_value_cr, 1),
        "close": round(close, 2),
        "patterns": ",".join(bull_patterns + bear_patterns) or "-",
        "structure": structure,
        "rsi": round(rsi, 1), "adx": round(last["adx"], 1),
        "vol_ratio": round(vol_ratio, 2), "atr_pct": round(last["atr_pct"], 2),
        "near_support": near_sup["price"] if near_sup else None,
        "near_resistance": near_res["price"] if near_res else None,
        "confirmations": conf,
        "plan": plan,
        # carried for charting:
        "_df": df, "_levels": levels, "_trendlines": tls,
    }
