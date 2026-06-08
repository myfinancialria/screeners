#!/usr/bin/env python3
"""
End-of-day REPLAY of the option-buying strategies — the hands-off engine.

Runs once after market close (in GitHub Actions). For each underlying it pulls the
day's 5-min intraday candles and steps through them chronologically, feeding the
SAME strategies.py logic a point-in-time Context at each bar (no look-ahead), then:
  • opens a paper option-buy when a strategy triggers (fill = that bar's option close),
  • monitors open trades intrabar (option/index bar high-low) for target / stop /
    time-exit,
  • squares off anything still open at the last bar of the session.

Everything is written to the same strategy.db journal that build_journal.py renders,
so the public report is identical to the live path — just produced deterministically.

    python3 eod_simulate.py                 # replay today (IST)
    python3 eod_simulate.py --date 2026-06-06
    python3 eod_simulate.py --capital 500000

Why replay instead of a 6-hour live job? GitHub Actions can't host a continuous
intraday process; a short post-close replay on the day's real candles is hands-off,
free and reproducible. Same-day option-premium history is available; expired
contracts are not, so this is designed to run each day going forward.
"""
from __future__ import annotations

import argparse
import datetime as dt

import pandas as pd

import journal_db as J
import strategies as S
from fyers_data import resolve_fo
from history import get_history
from live_trade import (FUT_STRATEGIES, HARD_STOP_PCT, IST, STOCK_FUTURES,
                        UNDERLYINGS, atm_strike, fetch_oi,
                        nearest_option_expiry_epoch, size_lots, size_lots_fut)

NO_NEW_ENTRY_AFTER = dt.time(15, 20)   # stop opening fresh trades near the close
SQUAREOFF_AFTER = dt.time(15, 25)
# Fallback weekly-expiry weekday for PAST dates (master only knows live expiries).
EXPIRY_WEEKDAY = {"NIFTY": 3, "SENSEX": 3}   # Thu; only used when replaying past days


def _today_rows(df, date):
    if df is None or df.empty:
        return df
    return df[[ix.date() == date for ix in df.index]]


def _row_at(df, t):
    """The candle at time t (exact, else the most recent one at/just before t)."""
    if df is None or df.empty:
        return None
    if t in df.index:
        return df.loc[t]
    prior = df[df.index <= t]
    return prior.iloc[-1] if not prior.empty else None


def is_expiry_day(name, exch, date, today):
    if date == today:
        ep = nearest_option_expiry_epoch(name, exch)
        return bool(ep and dt.datetime.fromtimestamp(ep, IST).date() == date)
    return date.weekday() == EXPIRY_WEEKDAY.get(name, 3)


def intrabar_exit(pos, idx_c, opt_c, t):
    """Return (exit_price, reason) if this bar trips an exit, else (None, None).
    For options idx_c is the index bar and opt_c the option bar; for futures both
    are the same future bar. `bullish` = long the underlying move (CE or LONG)."""
    bullish = pos["opt_kind"] == "CE" or pos.get("side") == "LONG"
    if pos["sl_prem"] is not None and opt_c["low"] <= pos["sl_prem"]:
        return pos["sl_prem"], f"Stop hit — premium ₹{pos['sl_prem']:.2f} touched"
    if pos["target_prem"] is not None and opt_c["high"] >= pos["target_prem"]:
        return pos["target_prem"], f"Target hit — premium ₹{pos['target_prem']:.2f} touched"
    if pos["sl_spot"] is not None:
        if bullish and idx_c["low"] <= pos["sl_spot"]:
            return float(opt_c["close"]), f"Stop hit — price {pos['sl_spot']:.1f} touched"
        if not bullish and idx_c["high"] >= pos["sl_spot"]:
            return float(opt_c["close"]), f"Stop hit — price {pos['sl_spot']:.1f} touched"
    if pos["target_spot"] is not None:
        if bullish and idx_c["high"] >= pos["target_spot"]:
            return float(opt_c["close"]), f"Target hit — price {pos['target_spot']:.1f} touched"
        if not bullish and idx_c["low"] <= pos["target_spot"]:
            return float(opt_c["close"]), f"Target hit — price {pos['target_spot']:.1f} touched"
    if pos["time_exit_min"]:
        if (t - pos["entry_t"]).total_seconds() >= pos["time_exit_min"] * 60:
            return float(opt_c["close"]), f"Time exit — {pos['time_exit_min']} min elapsed"
    return None, None


def replay_underlying(u, date, today):
    name, exch, spot_sym = u["name"], u["exchange"], u["spot"]
    date_str = date.strftime("%Y-%m-%d")

    idx = _today_rows(get_history(spot_sym, "5", days=6), date)
    if idx is None or idx.empty:
        print(f"  · {name}: no intraday data for {date_str} (holiday/weekend?)")
        return
    today_open = float(idx["open"].iloc[0])

    daily = get_history(spot_sym, "D", days=14)
    prior = daily[[ix.date() < date for ix in daily.index]]
    prev_close = float(prior["close"].iloc[-1]) if not prior.empty else today_open

    try:
        fut_sym, _, _ = resolve_fo(name, "FUT", exchange=exch)
        fut = _today_rows(get_history(fut_sym, "5", days=6), date)
    except (Exception, SystemExit):
        fut = None

    is_exp = is_expiry_day(name, exch, date, today)
    oi = fetch_oi(spot_sym) if date == today else None   # OI snapshot only meaningful live
    capital = J.get_capital()

    opt_cache = {}                 # symbol -> today's 5-min premium DataFrame
    open_pos = {}                  # strategy -> in-memory position dict
    flags = "  [EXPIRY]" if is_exp else ""
    print(f"  · {name} {date_str}: {len(idx)} bars, prev_close {prev_close:.1f}{flags}")

    times = list(idx.index)
    for i, t in enumerate(times):
        idx_c = idx.iloc[i]
        eval_now = t + dt.timedelta(minutes=5)        # candle t has just closed
        idx_upto = idx.iloc[: i + 1]
        fut_upto = fut[fut.index <= t] if fut is not None else None

        # 1) manage open positions on this bar
        for strat in list(open_pos):
            pos = open_pos[strat]
            opt_c = _row_at(opt_cache[pos["opt_symbol"]], t)
            if opt_c is None:
                continue
            ex_prem, reason = intrabar_exit(pos, idx_c, opt_c, t)
            if ex_prem is not None:
                J.close_trade(pos["id"], t.isoformat(timespec="seconds"),
                              float(idx_c["close"]), float(ex_prem), reason)
                print(f"      ✕ {strat} {pos['opt_kind']} exit @ ₹{ex_prem:.2f} — {reason}")
                del open_pos[strat]

        # 2) look for fresh entries (not too close to the bell)
        if t.time() > NO_NEW_ENTRY_AFTER:
            continue
        ctx = S.Context(now=eval_now, underlying=name, exchange=exch,
                        spot=float(idx_c["close"]), idx5=idx_upto, fut5=fut_upto,
                        prev_close=prev_close, today_open=today_open,
                        is_expiry=is_exp, oi=oi)
        for strat, fn in S.STRATEGIES.items():
            if strat in open_pos or J.has_trade_today(date_str, strat, name):
                continue
            try:
                sig = fn(ctx)
            except Exception as e:
                print(f"      ! {strat} error: {e}")
                continue
            if not sig:
                continue
            pos = _enter(ctx, strat, sig, t, capital, opt_cache)
            if pos:
                open_pos[strat] = pos

    # 3) square off whatever remains at the last available bar
    if open_pos:
        t = times[-1]
        idx_c = idx.iloc[-1]
        for strat, pos in open_pos.items():
            opt_c = _row_at(opt_cache[pos["opt_symbol"]], t)
            ex_prem = float(opt_c["close"]) if opt_c is not None else pos["entry_prem"]
            J.close_trade(pos["id"], t.isoformat(timespec="seconds"),
                          float(idx_c["close"]), ex_prem, "EOD square-off (intraday)")
            print(f"      ✕ {strat} {pos['opt_kind']} EOD square-off @ ₹{ex_prem:.2f}")


def _enter(ctx, strat, sig, t, capital, opt_cache):
    try:
        opt_sym, lot_size, _ = resolve_fo(
            ctx.underlying, sig.opt_kind,
            strike=atm_strike(ctx.underlying, ctx.spot), exchange=ctx.exchange)
    except (Exception, SystemExit) as e:
        print(f"      ! {strat}: cannot resolve option ({e})")
        return None
    if opt_sym not in opt_cache:
        try:
            opt_cache[opt_sym] = _today_rows(get_history(opt_sym, "5", days=6), t.date())
        except Exception as e:
            print(f"      ! {strat}: no premium history for {opt_sym} ({e})")
            opt_cache[opt_sym] = pd.DataFrame()
    opt_c = _row_at(opt_cache[opt_sym], t)
    if opt_c is None:
        print(f"      ! {strat}: no premium bar at {t:%H:%M} for {opt_sym}")
        return None
    prem = float(opt_c["close"])
    if prem <= 0:
        return None

    sl_prem = prem * (1 - (sig.sl_prem_pct if sig.sl_prem_pct else HARD_STOP_PCT))
    target_prem = (prem + sig.target_prem_pts) if sig.target_prem_pts else None
    lots = size_lots(capital, prem, sl_prem, lot_size)
    qty = lots * lot_size
    risk_amt = (prem - sl_prem) * qty
    sl_note = sig.sl_logic
    if sig.sl_prem_pct is None:
        sl_note += f"  Premium safety-net stop at {HARD_STOP_PCT*100:.0f}% (₹{sl_prem:.2f})."

    tid = J.open_trade(
        date=t.strftime("%Y-%m-%d"), strategy=strat, underlying=ctx.underlying,
        exchange=ctx.exchange, opt_symbol=opt_sym, opt_kind=sig.opt_kind, strike=None,
        lots=lots, lot_size=lot_size, qty=qty, entry_ts=t.isoformat(timespec="seconds"),
        entry_spot=ctx.spot, entry_prem=prem, sl_spot=sig.sl_spot,
        target_spot=sig.target_spot, sl_prem=sl_prem, target_prem=target_prem,
        time_exit_min=sig.time_exit_min, risk_amt=risk_amt,
        entry_remarks=sig.entry_remarks, entry_logic=sig.entry_logic,
        sl_logic=sl_note, exit_logic=sig.exit_logic)
    print(f"      ➜ {strat} {sig.opt_kind} {opt_sym} {lots}×{lot_size} @ ₹{prem:.2f} "
          f"(#{tid}) — {t:%H:%M}")
    return {"id": tid, "opt_symbol": opt_sym, "opt_kind": sig.opt_kind,
            "entry_prem": prem, "entry_t": t, "sl_prem": sl_prem,
            "target_prem": target_prem, "sl_spot": sig.sl_spot,
            "target_spot": sig.target_spot, "time_exit_min": sig.time_exit_min}


def _enter_fut(ctx, strat, sig, t, capital, price_cache):
    if sig.sl_spot is None or sig.target_spot is None:
        return None
    fut_sym = next(iter(price_cache))               # the one future being replayed
    bar = _row_at(price_cache[fut_sym], t)
    if bar is None:
        return None
    price = float(bar["close"])
    if price <= 0:
        return None
    lot_size = price_cache["__lot__"]
    side = "LONG" if sig.opt_kind == "CE" else "SHORT"
    lots = size_lots_fut(capital, price, sig.sl_spot, lot_size)
    qty = lots * lot_size
    risk_amt = abs(price - sig.sl_spot) * qty
    remark = (sig.entry_remarks.replace("Bought ATM CE.", f"Went LONG {ctx.underlying} future.")
              .replace("Bought ATM PE.", f"Went SHORT {ctx.underlying} future."))
    tid = J.open_trade(
        date=t.strftime("%Y-%m-%d"), strategy=strat, underlying=ctx.underlying,
        exchange="NSE", instrument_type="FUT", opt_symbol=fut_sym, opt_kind="FUT",
        side=side, strike=None, lots=lots, lot_size=lot_size, qty=qty,
        entry_ts=t.isoformat(timespec="seconds"), entry_spot=price, entry_prem=price,
        sl_spot=sig.sl_spot, target_spot=sig.target_spot, sl_prem=None, target_prem=None,
        time_exit_min=None, risk_amt=risk_amt, entry_remarks=remark,
        entry_logic=sig.entry_logic, sl_logic=sig.sl_logic, exit_logic=sig.exit_logic)
    print(f"      ➜ {strat} {ctx.underlying} FUT {side} {lots}×{lot_size} @ ₹{price:.2f} "
          f"(#{tid}) — {t:%H:%M}")
    return {"id": tid, "opt_symbol": fut_sym, "opt_kind": "FUT", "side": side,
            "entry_prem": price, "entry_t": t, "sl_prem": None, "target_prem": None,
            "sl_spot": sig.sl_spot, "target_spot": sig.target_spot, "time_exit_min": None}


def replay_future(stock, date, today):
    date_str = date.strftime("%Y-%m-%d")
    try:
        fut_sym, lot_size, _ = resolve_fo(stock, "FUT", exchange="NSE")
    except (Exception, SystemExit) as e:
        print(f"  · {stock} FUT: no contract ({e})")
        return
    fut = _today_rows(get_history(fut_sym, "5", days=6), date)
    if fut is None or fut.empty:
        print(f"  · {stock} FUT: no intraday data for {date_str}")
        return
    today_open = float(fut["open"].iloc[0])
    daily = get_history(fut_sym, "D", days=20)
    prior = daily[[ix.date() < date for ix in daily.index]]
    prev_close = float(prior["close"].iloc[-1]) if not prior.empty else today_open
    capital = J.get_capital()
    price_cache = {fut_sym: fut, "__lot__": lot_size}   # future is its own price/fill series
    open_pos = {}
    print(f"  · {stock} FUT {date_str}: {len(fut)} bars, prev_close {prev_close:.1f}")

    times = list(fut.index)
    for i, t in enumerate(times):
        bar = fut.iloc[i]
        eval_now = t + dt.timedelta(minutes=5)
        fut_upto = fut.iloc[: i + 1]
        for strat in list(open_pos):
            pos = open_pos[strat]
            ex_prem, reason = intrabar_exit(pos, bar, bar, t)  # idx_c == opt_c == future bar
            if ex_prem is not None:
                J.close_trade(pos["id"], t.isoformat(timespec="seconds"),
                              float(bar["close"]), float(ex_prem), reason)
                print(f"      ✕ {strat} {pos['side']} exit @ ₹{ex_prem:.2f} — {reason}")
                del open_pos[strat]
        if t.time() > NO_NEW_ENTRY_AFTER:
            continue
        ctx = S.Context(now=eval_now, underlying=stock, exchange="NSE",
                        spot=float(bar["close"]), idx5=fut_upto, fut5=fut_upto,
                        prev_close=prev_close, today_open=today_open,
                        is_expiry=False, oi=None)
        for strat in FUT_STRATEGIES:
            if strat in open_pos or J.has_trade_today(date_str, strat, stock):
                continue
            try:
                sig = S.STRATEGIES[strat](ctx)
            except Exception as e:
                print(f"      ! {strat} error: {e}")
                continue
            if not sig:
                continue
            pos = _enter_fut(ctx, strat, sig, t, capital, price_cache)
            if pos:
                open_pos[strat] = pos

    if open_pos:
        t, bar = times[-1], fut.iloc[-1]
        for strat, pos in open_pos.items():
            J.close_trade(pos["id"], t.isoformat(timespec="seconds"),
                          float(bar["close"]), float(bar["close"]), "EOD square-off (intraday)")
            print(f"      ✕ {strat} {pos['side']} EOD square-off @ ₹{bar['close']:.2f}")


def main():
    ap = argparse.ArgumentParser(description="EOD replay of option-buying strategies")
    ap.add_argument("--date", help="YYYY-MM-DD (IST); default today")
    ap.add_argument("--capital", type=float, help="set virtual capital (₹) before run")
    args = ap.parse_args()

    J.init()
    if args.capital:
        J.set_capital(args.capital)
    today = dt.datetime.now(IST).date()
    date = dt.date.fromisoformat(args.date) if args.date else today
    if date.weekday() >= 5:
        print(f"{date} is a weekend — nothing to replay.")
        return

    print(f"EOD replay {date} | capital ₹{J.get_capital():,.0f}")
    print(f"  index options: {', '.join(u['name'] for u in UNDERLYINGS)}")
    for u in UNDERLYINGS:
        replay_underlying(u, date, today)
    print(f"  stock futures (long+short): {', '.join(STOCK_FUTURES)}")
    for stock in STOCK_FUTURES:
        replay_future(stock, date, today)
    print("Done. Run build_journal.py to refresh the report.")


if __name__ == "__main__":
    main()
