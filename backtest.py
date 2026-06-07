"""
Event-driven portfolio backtest of the screener's bullish setups.

Rules (configurable):
  capital ₹50,00,000 | max 25 concurrent positions | max 4% equity per stock |
  max ₹10,000 risk per stock.

Methodology:
  - Universe scanned once (one history fetch per stock); signals computed
    point-in-time (rolling indicators only use past data -> no look-ahead).
  - Entry at the signal bar's CLOSE for BREAKOUT and PULLBACK setups.
  - SL = entry - 1.5*ATR(14);  Target = entry + 2*risk  (1:2 R:R).
  - Position size = min(floor(10000/risk_per_share), floor(4%*equity/price));
    skipped if 0 / cash short / 25 slots full.
  - Exit: day low <= SL (SL hit) -> SL ; elif day high >= target -> target ;
    elif held >= max_hold bars -> time exit at close ; else still OPEN (MTM).
  - Equity (daily) = cash + open positions marked to that day's close.

run_backtest(...) -> {trades, equity (date->value), stats, params}
"""
import datetime as dt

import numpy as np
import pandas as pd

from candles import detect_patterns
from history import get_history
from indicators import add_indicators

CAPITAL = 5_000_000.0
MAX_POSITIONS = 25
MAX_ALLOC_PCT = 4.0          # % of equity per stock
MAX_RISK = 10_000.0          # ₹ risk per stock
ATR_MULT_SL = 1.5
RR = 2.0                     # initial target = entry + RR * risk
MAX_HOLD = 60               # trading-day time stop
COST_PCT = 0.05             # per side (% of trade value)
TRIM_PCT = 0.25             # book this fraction at the initial target
ATR_TRAIL = 3.0             # Chandelier trail for the runner (highest high - n*ATR)


def _signals_for(df: pd.DataFrame) -> pd.DataFrame:
    """Vectorized point-in-time entry signals for one stock."""
    d = add_indicators(df)
    o, h, l, c = d["open"], d["high"], d["low"], d["close"]
    trend = (d["ema20"] > d["ema50"]) & (c > d["ema50"])
    above200 = (c > d["ema200"]) | d["ema200"].isna()
    trend &= above200

    breakout = (c > d["resistance_20"]) & (d["vol_ratio"] >= 1.5) & trend
    pullback = trend & (l <= d["ema20"] * 1.02) & (c > d["ema20"]) & (c > o) \
        & d["rsi"].between(45, 65)

    d["entry_signal"] = (breakout | pullback) & (d["atr"] > 0)
    d["setup"] = np.where(breakout, "BREAKOUT", np.where(pullback, "PULLBACK", ""))

    # reversal sign used to cut the runner
    d = detect_patterns(d)
    d["bear_rev"] = d["bearish_engulfing"] | d["shooting_star"] | d["evening_star"]
    return d


def _entry_reason(row):
    if row["setup"] == "BREAKOUT":
        return (f"Breakout: close {row['close']:.1f} > 20-bar high "
                f"{row['resistance_20']:.1f} on {row['vol_ratio']:.1f}x volume, "
                f"EMA20>EMA50, RSI {row['rsi']:.0f}")
    return (f"Pullback to EMA20 {row['ema20']:.1f} in uptrend, bullish close "
            f"{row['close']:.1f}, RSI {row['rsi']:.0f}")


def run_backtest(symbols, days=500, capital=CAPITAL, max_positions=MAX_POSITIONS,
                 max_alloc_pct=MAX_ALLOC_PCT, max_risk=MAX_RISK, max_hold=MAX_HOLD,
                 cost_pct=COST_PCT, trim_pct=TRIM_PCT, atr_trail=ATR_TRAIL, progress=True):
    from concurrent.futures import ThreadPoolExecutor, as_completed

    # 1) fetch + compute signals per stock
    stock = {}   # symbol -> df indexed by date (normalized)
    events = []  # candidate entries

    def load(sym):
        try:
            df = get_history(sym, resolution="D", days=days)
            if df.empty or len(df) < 220:
                return None
            d = _signals_for(df)
            d.index = d.index.normalize()
            return sym, d
        except Exception:
            return None

    syms = list(symbols)
    done = 0
    with ThreadPoolExecutor(max_workers=10) as ex:
        for fut in as_completed([ex.submit(load, s) for s in syms]):
            done += 1
            r = fut.result()
            if progress and (done % 100 == 0 or done == len(syms)):
                print(f"  loaded {done}/{len(syms)}")
            if not r:
                continue
            sym, d = r
            stock[sym] = d
            sig = d[d["entry_signal"]]
            for ts, row in sig.iterrows():
                risk_ps = ATR_MULT_SL * row["atr"]
                entry = float(row["close"])
                events.append({
                    "date": ts, "symbol": sym, "entry": entry,
                    "risk_ps": float(risk_ps),
                    "sl": round(entry - risk_ps, 2),
                    "target": round(entry + RR * risk_ps, 2),
                    "setup": row["setup"], "vol_ratio": float(row["vol_ratio"]),
                    "reason": _entry_reason(row),
                })

    # 2) portfolio simulation over the unified timeline
    all_dates = sorted({ts for d in stock.values() for ts in d.index})
    date_idx = {ts: i for i, ts in enumerate(all_dates)}
    events_by_date = {}
    for e in events:
        events_by_date.setdefault(e["date"], []).append(e)

    cash = capital
    realized = 0.0
    open_pos = {}     # symbol -> position dict
    trades = []
    equity = {}

    def bar(sym, ts):
        d = stock[sym]
        if ts in d.index:
            r = d.loc[ts]
            return (float(r["high"]), float(r["low"]), float(r["close"]),
                    float(r["ema20"]), float(r["atr"]), bool(r["bear_rev"]))
        return None

    def close_on(sym, ts):
        b = bar(sym, ts)
        return b[2] if b else None

    def record_exit(pos, ts, price, reason, i):
        """Close the remaining qty; fold in any earlier trim for one trade row."""
        nonlocal cash, realized
        cash += pos["qty"] * price * (1 - cost_pct / 100)
        trim = pos.get("trim_info")
        net_final = pos["qty"] * price * (1 - cost_pct / 100)
        net_trim = trim["proceeds"] if trim else 0.0
        pnl = net_final + net_trim - pos["cost_basis"]
        if trim:
            reason = (f"Trimmed {trim['qty']} (25%) @ {trim['price']} on "
                      f"{trim['date']:%d-%b}; runner {reason}")
        realized += pnl
        trades.append({**pos, "exit_date": ts, "exit_price": round(price, 2),
                       "exit_reason": reason, "status": "CLOSED",
                       "pnl": round(pnl, 2),
                       "ret_pct": round(pnl / pos["cost_basis"] * 100, 2),
                       "r_multiple": round(pnl / pos["risk_amt"], 2),
                       "bars_held": i - pos["entry_i"]})
        del open_pos[pos["symbol"]]

    for i, ts in enumerate(all_dates):
        # ---- exits / scale-out first ----
        for sym in list(open_pos):
            pos = open_pos[sym]
            b = bar(sym, ts)
            if b is None:
                continue
            hi, lo, cl, ema20, atr, bear = b

            if not pos["trimmed"]:
                if lo <= pos["sl"]:
                    record_exit(pos, ts, pos["sl"], f"SL hit @ {pos['sl']}", i)
                elif hi >= pos["target"]:
                    trim_qty = int(round(pos["initial_qty"] * trim_pct))
                    if trim_qty < 1 or trim_qty >= pos["qty"]:
                        record_exit(pos, ts, pos["target"],
                                    f"target @ {pos['target']} (full exit)", i)
                    else:
                        proceeds = trim_qty * pos["target"] * (1 - cost_pct / 100)
                        cash += proceeds
                        pos["qty"] -= trim_qty
                        pos["trimmed"] = True
                        pos["sl"] = pos["entry"]           # runner to breakeven
                        pos["highest_high"] = max(hi, pos["entry"])
                        pos["trim_info"] = {"qty": trim_qty, "price": pos["target"],
                                            "date": ts, "proceeds": proceeds,
                                            "proceeds_gross": trim_qty * pos["target"]}
                elif i - pos["entry_i"] >= max_hold:
                    record_exit(pos, ts, round(cl, 2), f"time exit ({max_hold}d)", i)
            else:
                # runner: trail the highest high (Chandelier), cut on reversal
                pos["highest_high"] = max(pos["highest_high"], hi)
                trail = max(pos["entry"], pos["highest_high"] - atr_trail * atr)
                pos["sl"] = max(pos["sl"], trail)
                if lo <= pos["sl"]:
                    record_exit(pos, ts, pos["sl"], f"trail-stop @ {round(pos['sl'],2)}", i)
                elif cl < ema20 or bear:
                    why = "close<EMA20" if cl < ema20 else "bearish reversal candle"
                    record_exit(pos, ts, round(cl, 2), f"reversal exit ({why}) @ {cl:.2f}", i)
                elif i - pos["entry_i"] >= max_hold:
                    record_exit(pos, ts, round(cl, 2), f"time exit ({max_hold}d)", i)

        # ---- entries ----
        todays = sorted(events_by_date.get(ts, []), key=lambda e: e["vol_ratio"], reverse=True)
        for e in todays:
            if len(open_pos) >= max_positions or e["symbol"] in open_pos:
                continue
            equity_now = cash + sum(p["qty"] * (close_on(s, ts) or p["entry"])
                                    for s, p in open_pos.items())
            qty_risk = int(max_risk // e["risk_ps"]) if e["risk_ps"] > 0 else 0
            qty_cap = int((max_alloc_pct / 100 * equity_now) // e["entry"])
            qty = min(qty_risk, qty_cap)
            if qty < 1:
                continue
            cost_basis = qty * e["entry"] * (1 + cost_pct / 100)
            if cost_basis > cash:
                qty = int(cash // (e["entry"] * (1 + cost_pct / 100)))
                if qty < 1:
                    continue
                cost_basis = qty * e["entry"] * (1 + cost_pct / 100)
            cash -= cost_basis
            open_pos[e["symbol"]] = {
                "symbol": e["symbol"], "setup": e["setup"], "entry_date": ts,
                "entry": e["entry"], "qty": qty, "initial_qty": qty,
                "value": round(qty * e["entry"], 2),
                "sl": e["sl"], "target": e["target"], "risk_ps": round(e["risk_ps"], 2),
                "risk_amt": round(qty * e["risk_ps"], 2), "cost_basis": cost_basis,
                "entry_i": i, "reason": e["reason"],
                "trimmed": False, "highest_high": e["entry"], "trim_info": None,
            }

        # ---- mark to market ----
        mtm = sum(p["qty"] * (close_on(s, ts) or p["entry"])
                  for s, p in open_pos.items())
        equity[ts] = round(cash + mtm, 2)

    # open positions left at the end -> record as OPEN (marked to last close)
    last_ts = all_dates[-1] if all_dates else None
    for sym, pos in open_pos.items():
        cl = close_on(sym, last_ts) or pos["entry"]
        trim = pos.get("trim_info")
        net_trim = trim["proceeds"] if trim else 0.0
        pnl = pos["qty"] * cl + net_trim - pos["cost_basis"]
        reason = "Open (marked to market)"
        if trim:
            reason = (f"Trimmed {trim['qty']} (25%) @ {trim['price']} on "
                      f"{trim['date']:%d-%b}; runner open (MTM)")
        trades.append({**pos, "exit_date": None, "exit_price": round(cl, 2),
                       "exit_reason": reason, "status": "OPEN",
                       "pnl": round(pnl, 2),
                       "ret_pct": round(pnl / pos["cost_basis"] * 100, 2),
                       "r_multiple": round(pnl / pos["risk_amt"], 2),
                       "bars_held": len(all_dates) - 1 - pos["entry_i"]})

    stats = _stats(trades, equity, capital, all_dates, len(open_pos))
    return {"trades": sorted(trades, key=lambda t: t["entry_date"]),
            "equity": equity, "stats": stats,
            "params": {"capital": capital, "max_positions": max_positions,
                       "max_alloc_pct": max_alloc_pct, "max_risk": max_risk,
                       "atr_mult_sl": ATR_MULT_SL, "rr": RR, "max_hold": max_hold,
                       "cost_pct": cost_pct, "trim_pct": trim_pct,
                       "atr_trail": atr_trail, "universe_size": len(syms)}}


def _stats(trades, equity, capital, all_dates, open_count):
    closed = [t for t in trades if t["status"] == "CLOSED"]
    eq = pd.Series(equity).sort_index()
    end = float(eq.iloc[-1]) if len(eq) else capital
    wins = [t for t in closed if t["pnl"] > 0]
    losses = [t for t in closed if t["pnl"] <= 0]
    gross_win = sum(t["pnl"] for t in wins)
    gross_loss = abs(sum(t["pnl"] for t in losses))
    years = ((all_dates[-1] - all_dates[0]).days / 365.25) if len(all_dates) > 1 else 1
    # max drawdown
    mdd = 0.0
    if len(eq):
        peak = eq.cummax()
        mdd = float(((eq - peak) / peak).min() * 100)
    return {
        "start_capital": capital, "end_equity": round(end, 2),
        "total_return_pct": round((end / capital - 1) * 100, 2),
        "cagr_pct": round(((end / capital) ** (1 / years) - 1) * 100, 2) if years > 0 else 0,
        "total_trades": len(trades), "closed_trades": len(closed), "open_trades": open_count,
        "win_rate_pct": round(len(wins) / len(closed) * 100, 1) if closed else 0,
        "avg_win": round(gross_win / len(wins), 0) if wins else 0,
        "avg_loss": round(-gross_loss / len(losses), 0) if losses else 0,
        "profit_factor": round(gross_win / gross_loss, 2) if gross_loss else float("inf"),
        "avg_r": round(np.mean([t["r_multiple"] for t in closed]), 2) if closed else 0,
        "max_drawdown_pct": round(mdd, 2),
        "avg_hold_days": round(np.mean([t["bars_held"] for t in closed]), 1) if closed else 0,
        "period": f"{all_dates[0]:%d %b %Y} – {all_dates[-1]:%d %b %Y}" if all_dates else "-",
    }


if __name__ == "__main__":
    import argparse
    from universe import resolve_universe
    ap = argparse.ArgumentParser()
    ap.add_argument("--universe", default="nifty50")
    ap.add_argument("--days", type=int, default=500)
    args = ap.parse_args()
    res = run_backtest(resolve_universe(args.universe), days=args.days)
    s = res["stats"]
    print(f"\n{s['period']} | trades {s['total_trades']} (closed {s['closed_trades']}, "
          f"open {s['open_trades']})")
    print(f"End equity ₹{s['end_equity']:,.0f} | return {s['total_return_pct']}% | "
          f"CAGR {s['cagr_pct']}% | win {s['win_rate_pct']}% | PF {s['profit_factor']} | "
          f"avgR {s['avg_r']} | maxDD {s['max_drawdown_pct']}%")
