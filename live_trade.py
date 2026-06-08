#!/usr/bin/env python3
"""
LIVE paper-trading engine for the researched option-BUYING strategies.

Foreground loop: every `--interval` seconds during market hours it pulls live
NIFTY (NSE) + SENSEX (BSE) data, evaluates ORB / 5-EMA / Expiry-Gamma / OI-Gap,
opens *paper* option-buy positions (ATM CE/PE) with a planned target & stop, and
monitors open positions for target / stop / time-exit — journalling every entry
and exit with the full rationale.

    python3 live_trade.py                 # run with defaults (60s poll, market hours)
    python3 live_trade.py --interval 120  # slower poll
    python3 live_trade.py --once          # single evaluation pass, then exit
    python3 live_trade.py --no-clock      # ignore market-hours gate (for testing now)
    python3 live_trade.py --capital 500000

Nothing is sent to the broker. All trades live in strategy.db; build_journal.py
turns them into the performance + journal report.  Ctrl-C stops cleanly (open
paper trades stay open and are picked up next run / squared off at EOD).
"""
from __future__ import annotations

import argparse
import datetime as dt
import time
from zoneinfo import ZoneInfo

import journal_db as J
import strategies as S
from fyers_data import C_EXPIRY, C_OPTTYPE, _load_rows, get_fyers, ltp, resolve_fo
from history import get_history

IST = ZoneInfo("Asia/Kolkata")

# ── risk / sizing config ───────────────────────────────────────────────────────
RISK_PCT = 0.01          # risk ~1% of capital per trade
HARD_STOP_PCT = 0.40     # premium safety-net stop for spot-based strategies
MAX_DEPLOY_PCT = 0.50    # never spend >50% of capital on a single position
SQUAREOFF = dt.time(15, 25)
MARKET_OPEN = dt.time(9, 15)
MARKET_CLOSE = dt.time(15, 30)

UNDERLYINGS = [
    {"name": "NIFTY",  "exchange": "NSE", "spot": "NSE:NIFTY50-INDEX", "step": 50},
    {"name": "SENSEX", "exchange": "BSE", "spot": "BSE:SENSEX-INDEX", "step": 100},
]
_STEP = {u["name"]: u["step"] for u in UNDERLYINGS}


def atm_strike(name, spot):
    """Nearest ATM strike from the live index level (SENSEX has no futures to proxy
    spot, so resolve_fo's own 'ATM' path can't be used — we compute it directly)."""
    step = _STEP.get(name, 50)
    return int(round(spot / step) * step)


# ── small live-data helpers ────────────────────────────────────────────────────
def now_ist() -> dt.datetime:
    return dt.datetime.now(IST)


def today_str(now: dt.datetime) -> str:
    return now.strftime("%Y-%m-%d")


def nearest_option_expiry_epoch(underlying: str, exchange: str):
    """Epoch of the nearest CE/PE expiry, or None."""
    floor = time.time() - 86400
    exps = [float(r[C_EXPIRY]) for r in _load_rows(underlying, exchange)
            if r[C_OPTTYPE].strip().upper() in ("CE", "PE") and float(r[C_EXPIRY]) >= floor]
    return min(exps) if exps else None


def fetch_oi(spot_symbol: str):
    """Highest Call-OI strike (resistance) and Put-OI strike (support), or None."""
    try:
        resp = get_fyers().optionchain(
            data={"symbol": spot_symbol, "strikecount": 10, "timestamp": ""})
    except Exception:
        return None
    if not isinstance(resp, dict) or resp.get("s") != "ok":
        return None
    chain = resp.get("data", {}).get("optionsChain", []) or []
    calls = [(r.get("strike_price"), r.get("oi") or 0) for r in chain
             if r.get("option_type") == "CE" and r.get("strike_price")]
    puts = [(r.get("strike_price"), r.get("oi") or 0) for r in chain
            if r.get("option_type") == "PE" and r.get("strike_price")]
    if not calls or not puts:
        return None
    return {"call_res": max(calls, key=lambda x: x[1])[0],
            "put_sup": max(puts, key=lambda x: x[1])[0]}


def _today_rows(df, now):
    if df is None or df.empty:
        return df
    return df[[ix.date() == now.date() for ix in df.index]]


# ── context build ──────────────────────────────────────────────────────────────
_daily_cache = {}   # (underlying, date) -> prev_close
_fut_cache = {}     # (underlying, date) -> fut_symbol
_exp_cache = {}     # (underlying, date) -> is_expiry


def build_context(u: dict, now: dt.datetime):
    name, exch, spot_sym = u["name"], u["exchange"], u["spot"]
    dkey = (name, now.date())

    spot_map = ltp(spot_sym)
    spot = spot_map.get(spot_sym)
    if not spot:
        return None

    idx5 = _today_rows(get_history(spot_sym, "5", days=3), now)
    if idx5 is None or idx5.empty:
        return None
    today_open = float(idx5["open"].iloc[0])

    # prev close (cached for the day)
    if dkey not in _daily_cache:
        daily = get_history(spot_sym, "D", days=12)
        prev = daily[[ix.date() < now.date() for ix in daily.index]]
        _daily_cache[dkey] = float(prev["close"].iloc[-1]) if not prev.empty else today_open
    prev_close = _daily_cache[dkey]

    # futures (for VWAP / volume) — cached symbol, fresh candles
    if dkey not in _fut_cache:
        try:
            fut_sym, _, _ = resolve_fo(name, "FUT", exchange=exch)
        except (Exception, SystemExit):
            fut_sym = None
        _fut_cache[dkey] = fut_sym
    fut_sym = _fut_cache[dkey]
    try:
        fut5 = _today_rows(get_history(fut_sym, "5", days=3), now) if fut_sym else None
    except Exception:
        fut5 = None

    # expiry flag (cached for the day)
    if dkey not in _exp_cache:
        exp = nearest_option_expiry_epoch(name, exch)
        _exp_cache[dkey] = bool(exp and dt.datetime.fromtimestamp(exp, IST).date() == now.date())
    is_expiry = _exp_cache[dkey]

    oi = fetch_oi(spot_sym)

    return S.Context(now=now, underlying=name, exchange=exch, spot=float(spot),
                     idx5=idx5, fut5=fut5, prev_close=prev_close, today_open=today_open,
                     is_expiry=is_expiry, oi=oi)


# ── sizing ─────────────────────────────────────────────────────────────────────
def size_lots(capital, entry_prem, sl_prem, lot_size):
    risk_per_unit = max(entry_prem - sl_prem, 0.05 * entry_prem)
    by_risk = int((capital * RISK_PCT) // (risk_per_unit * lot_size))
    by_cost = int((capital * MAX_DEPLOY_PCT) // (entry_prem * lot_size))
    return max(1, min(max(1, by_risk), max(1, by_cost)))


# ── entry ──────────────────────────────────────────────────────────────────────
def try_enter(ctx, strat_name, sig, now):
    date = today_str(now)
    if J.has_trade_today(date, strat_name, ctx.underlying):
        return  # one trade per strategy/underlying/day

    try:
        opt_sym, lot_size, expiry = resolve_fo(
            ctx.underlying, sig.opt_kind,
            strike=atm_strike(ctx.underlying, ctx.spot), exchange=ctx.exchange)
    except (Exception, SystemExit) as e:
        print(f"    ! {strat_name}/{ctx.underlying}: could not resolve option ({e})")
        return
    prem = ltp(opt_sym).get(opt_sym)
    if not prem:
        print(f"    ! {strat_name}/{ctx.underlying}: no premium for {opt_sym}")
        return

    sl_prem = prem * (1 - (sig.sl_prem_pct if sig.sl_prem_pct else HARD_STOP_PCT))
    target_prem = (prem + sig.target_prem_pts) if sig.target_prem_pts else None
    capital = J.get_capital()
    lots = size_lots(capital, prem, sl_prem, lot_size)
    qty = lots * lot_size
    risk_amt = (prem - sl_prem) * qty

    sl_note = sig.sl_logic
    if sig.sl_prem_pct is None:
        sl_note += f"  Premium safety-net stop at {HARD_STOP_PCT*100:.0f}% (₹{sl_prem:.2f})."

    tid = J.open_trade(
        date=date, strategy=strat_name, underlying=ctx.underlying, exchange=ctx.exchange,
        opt_symbol=opt_sym, opt_kind=sig.opt_kind, strike=None, lots=lots,
        lot_size=lot_size, qty=qty, entry_ts=now.isoformat(timespec="seconds"),
        entry_spot=ctx.spot, entry_prem=prem, sl_spot=sig.sl_spot,
        target_spot=sig.target_spot, sl_prem=sl_prem, target_prem=target_prem,
        time_exit_min=sig.time_exit_min, risk_amt=risk_amt,
        entry_remarks=sig.entry_remarks, entry_logic=sig.entry_logic,
        sl_logic=sl_note, exit_logic=sig.exit_logic)
    print(f"  ➜ ENTER #{tid} {strat_name} {ctx.underlying} {sig.opt_kind} {opt_sym} "
          f"{lots}lot×{lot_size} @ ₹{prem:.2f}  (risk ₹{risk_amt:,.0f})")
    print(f"      {sig.entry_remarks}")


# ── exit / monitor ─────────────────────────────────────────────────────────────
def check_exit(t, prem, spot, now):
    kind = t["opt_kind"]
    # premium stop (always present)
    if t["sl_prem"] is not None and prem <= t["sl_prem"]:
        return f"Stop hit — premium ₹{prem:.2f} ≤ stop ₹{t['sl_prem']:.2f}"
    # premium target
    if t["target_prem"] is not None and prem >= t["target_prem"]:
        return f"Target hit — premium ₹{prem:.2f} ≥ ₹{t['target_prem']:.2f}"
    # spot-based stop
    if t["sl_spot"] is not None:
        if kind == "CE" and spot <= t["sl_spot"]:
            return f"Stop hit — spot {spot:.1f} ≤ {t['sl_spot']:.1f}"
        if kind == "PE" and spot >= t["sl_spot"]:
            return f"Stop hit — spot {spot:.1f} ≥ {t['sl_spot']:.1f}"
    # spot-based target
    if t["target_spot"] is not None:
        if kind == "CE" and spot >= t["target_spot"]:
            return f"Target hit — spot {spot:.1f} ≥ {t['target_spot']:.1f}"
        if kind == "PE" and spot <= t["target_spot"]:
            return f"Target hit — spot {spot:.1f} ≤ {t['target_spot']:.1f}"
    # time exit
    if t["time_exit_min"]:
        entered = dt.datetime.fromisoformat(t["entry_ts"])
        if (now - entered).total_seconds() >= t["time_exit_min"] * 60:
            return f"Time exit — {t['time_exit_min']} min elapsed"
    return None


def monitor_open(spot_by_underlying, now, force_eod=False):
    for t in J.open_trades():
        prem = ltp(t["opt_symbol"]).get(t["opt_symbol"])
        if prem is None:
            continue
        spot = spot_by_underlying.get(t["underlying"])
        reason = "EOD square-off (intraday)" if force_eod else check_exit(t, prem, spot, now)
        if reason:
            J.close_trade(t["id"], now.isoformat(timespec="seconds"), spot, prem, reason)
            pnl = (prem - t["entry_prem"]) * t["qty"]
            print(f"  ✕ EXIT  #{t['id']} {t['strategy']} {t['underlying']} {t['opt_kind']} "
                  f"@ ₹{prem:.2f}  P&L ₹{pnl:,.0f}  — {reason}")


def square_off_all(now):
    """Force-close every open paper position at LTP (intraday discipline)."""
    if not J.open_trades():
        return
    spots = {u["name"]: ltp(u["spot"]).get(u["spot"]) for u in UNDERLYINGS}
    monitor_open(spots, now, force_eod=True)


# ── one pass ───────────────────────────────────────────────────────────────────
def run_pass(now):
    spot_by_underlying = {}
    contexts = []
    for u in UNDERLYINGS:
        ctx = build_context(u, now)
        if ctx is None:
            print(f"  · {u['name']}: no live data yet")
            continue
        spot_by_underlying[ctx.underlying] = ctx.spot
        contexts.append(ctx)
        flags = "  [EXPIRY]" if ctx.is_expiry else ""
        print(f"  · {ctx.underlying} spot {ctx.spot:.1f}{flags}")

    # monitor existing positions first (so a same-poll target/stop is honoured)
    monitor_open(spot_by_underlying, now)

    # then look for fresh entries
    for ctx in contexts:
        for strat_name, fn in S.STRATEGIES.items():
            try:
                sig = fn(ctx)
            except Exception as e:
                print(f"    ! {strat_name}/{ctx.underlying} error: {e}")
                continue
            if sig:
                try_enter(ctx, strat_name, sig, now)


# ── loop ───────────────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser(description="Live paper-trading engine (option buying)")
    ap.add_argument("--interval", type=int, default=60, help="poll seconds (default 60)")
    ap.add_argument("--once", action="store_true", help="single pass, then exit")
    ap.add_argument("--no-clock", action="store_true", help="ignore market-hours gate")
    ap.add_argument("--capital", type=float, help="set virtual capital (₹) before running")
    args = ap.parse_args()

    J.init()
    if args.capital:
        J.set_capital(args.capital)
    print(f"Live engine | capital ₹{J.get_capital():,.0f} | "
          f"strategies: {', '.join(S.STRATEGIES)} | underlyings: "
          f"{', '.join(u['name'] for u in UNDERLYINGS)}")
    print("Ctrl-C to stop.\n")

    squared_off = False
    try:
        while True:
            now = now_ist()
            t = now.time()
            weekday = now.weekday() < 5

            if args.no_clock:
                print(f"[{now:%H:%M:%S}] pass (clock gate off)")
                run_pass(now)
            elif weekday and MARKET_OPEN <= t < SQUAREOFF:
                print(f"[{now:%H:%M:%S}] pass")
                run_pass(now)
                squared_off = False
            elif weekday and SQUAREOFF <= t <= MARKET_CLOSE and not squared_off:
                print(f"[{now:%H:%M:%S}] {SQUAREOFF:%H:%M} square-off — closing all open paper trades")
                square_off_all(now)
                squared_off = True
            else:
                print(f"[{now:%H:%M:%S}] outside market hours — idle")

            if args.once:
                break
            time.sleep(max(5, args.interval))
    except KeyboardInterrupt:
        opens = len(J.open_trades())
        print(f"\nStopped. {opens} paper position(s) still open "
              f"(closed automatically at EOD next run). Run build_journal.py for the report.")


if __name__ == "__main__":
    main()
