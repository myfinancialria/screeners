"""
Option-BUYING strategy logic (NIFTY / SENSEX), faithful to the researched rules.

Each strategy is a pure function:  evaluate(ctx) -> Signal | None
It looks at the live market Context and, if its entry rule is met *right now*,
returns a Signal describing what to buy, the planned target & stop, and — crucially
for the journal — the plain-English remark + the entry/SL/exit *logic*.

It never places orders or touches the DB; live_trade.py does the execution and
records everything. SL / target are expressed on the SPOT (index) where the rule is
spot-based, and/or on the option PREMIUM where the rule is premium-based; the monitor
honours whichever are set.

Strategies implemented (all verified in the research pass):
  1. ORB           — Opening Range Breakout (Groww / Sahi)
  2. FIVE_EMA      — 5 EMA "Power of Stocks" (Subhasish Pani)
  3. EXPIRY_GAMMA  — expiry-day momentum gamma scalp (Sahi)
  4. OI_GAP        — Open-Interest + opening-gap directional (AlgoTest)
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Optional

import pandas as pd

from indicators import ema

# ── tunables (documented so the journal logic strings stay truthful) ───────────
OPENING_RANGE_MIN = 15      # first 15 minutes define the opening range
ORB_TARGET_MULT = 2.0       # ORB target = breakout ± 2× range width
FIVE_EMA_RR = 3.0           # 5-EMA target = 3× the risk (1:3)
GAMMA_TARGET_PTS = 25.0     # expiry gamma: +25 premium points
GAMMA_SL_PCT = 0.30         # expiry gamma: 30% of premium
GAMMA_TIME_EXIT_MIN = 30    # expiry gamma: flat within 30 minutes


@dataclass
class Signal:
    strategy: str
    opt_kind: str                       # "CE" | "PE"
    strike: str = "ATM"
    sl_spot: Optional[float] = None
    target_spot: Optional[float] = None
    sl_prem_pct: Optional[float] = None     # e.g. 0.30 -> stop at 30% premium loss
    target_prem_pts: Optional[float] = None # e.g. 25  -> book +25 premium points
    time_exit_min: Optional[int] = None
    entry_remarks: str = ""
    entry_logic: str = ""
    sl_logic: str = ""
    exit_logic: str = ""


@dataclass
class Context:
    """Everything a strategy needs for one underlying at one poll instant."""
    now: dt.datetime           # tz-aware IST
    underlying: str            # NIFTY | SENSEX
    exchange: str              # NSE | BSE
    spot: float                # live index level
    idx5: pd.DataFrame         # today's 5-min index candles (o/h/l/c, no volume)
    fut5: pd.DataFrame         # today's 5-min FUTURES candles (has volume) or empty
    prev_close: float          # yesterday's index close
    today_open: float          # today's index open
    is_expiry: bool            # is today the nearest-expiry day for this underlying?
    oi: Optional[dict] = None  # {"call_res": strike, "put_sup": strike} or None


# ── helpers ────────────────────────────────────────────────────────────────────
def _today_session(df: pd.DataFrame, now: dt.datetime) -> pd.DataFrame:
    """Rows belonging to today's session, strictly before `now`."""
    if df is None or df.empty:
        return df
    today = now.date()
    sub = df[[ix.date() == today for ix in df.index]]
    return sub[sub.index < now]


def _opening_range(idx5: pd.DataFrame, now: dt.datetime):
    """(high, low) of the opening range, or (None, None) if not complete yet."""
    if idx5 is None or idx5.empty:
        return None, None
    start = idx5.index[0].replace(hour=9, minute=15, second=0, microsecond=0)
    end = start + dt.timedelta(minutes=OPENING_RANGE_MIN)
    if now < end:                         # range still forming
        return None, None
    rng = idx5[(idx5.index >= start) & (idx5.index < end)]
    if rng.empty:
        return None, None
    return float(rng["high"].max()), float(rng["low"].min())


def _vwap(fut5: pd.DataFrame) -> Optional[float]:
    """Session VWAP from futures candles (index has no volume)."""
    if fut5 is None or fut5.empty or fut5["volume"].sum() == 0:
        return None
    tp = (fut5["high"] + fut5["low"] + fut5["close"]) / 3.0
    return float((tp * fut5["volume"]).cumsum().iloc[-1] / fut5["volume"].cumsum().iloc[-1])


# ── 1. Opening Range Breakout ──────────────────────────────────────────────────
def evaluate_orb(ctx: Context) -> Optional[Signal]:
    hi, lo = _opening_range(ctx.idx5, ctx.now)
    if hi is None:
        return None
    width = hi - lo
    if width <= 0:
        return None
    if ctx.spot > hi:
        tgt = hi + ORB_TARGET_MULT * width
        return Signal(
            "ORB", "CE", "ATM", sl_spot=lo, target_spot=tgt,
            entry_remarks=(f"Spot {ctx.spot:.1f} broke ABOVE the {OPENING_RANGE_MIN}-min "
                           f"opening-range high {hi:.1f} — first real upmove of the day, "
                           f"buying momentum. Bought ATM CE."),
            entry_logic=(f"ORB long: 09:15–09:30 range = [{lo:.1f}, {hi:.1f}] "
                         f"(width {width:.1f}); enter CE when spot closes above range high."),
            sl_logic=f"Stop = opposite boundary (range low {lo:.1f}); exit CE if spot falls back below it.",
            exit_logic=(f"Target = breakout + {ORB_TARGET_MULT:g}×range = {tgt:.1f} "
                        f"(~1:2 reward:risk)."))
    if ctx.spot < lo:
        tgt = lo - ORB_TARGET_MULT * width
        return Signal(
            "ORB", "PE", "ATM", sl_spot=hi, target_spot=tgt,
            entry_remarks=(f"Spot {ctx.spot:.1f} broke BELOW the {OPENING_RANGE_MIN}-min "
                           f"opening-range low {lo:.1f} — first real downmove, selling "
                           f"momentum. Bought ATM PE."),
            entry_logic=(f"ORB short: 09:15–09:30 range = [{lo:.1f}, {hi:.1f}] "
                         f"(width {width:.1f}); enter PE when spot closes below range low."),
            sl_logic=f"Stop = opposite boundary (range high {hi:.1f}); exit PE if spot climbs back above it.",
            exit_logic=(f"Target = breakdown − {ORB_TARGET_MULT:g}×range = {tgt:.1f} "
                        f"(~1:2 reward:risk)."))
    return None


# ── 2. 5 EMA "Power of Stocks" (Subhasish Pani) ────────────────────────────────
def evaluate_five_ema(ctx: Context) -> Optional[Signal]:
    df = _today_session(ctx.idx5, ctx.now)
    if df is None or len(df) < 7:
        return None
    e = ema(df["close"], 5)
    # P = immediately-prior candle (the potential "signal candle"); C = last closed candle
    p_hi, p_lo = float(df["high"].iloc[-2]), float(df["low"].iloc[-2])
    c_hi, c_lo = float(df["high"].iloc[-1]), float(df["low"].iloc[-1])
    e_p = float(e.iloc[-2])

    # BUY (CE): signal candle entirely BELOW the 5 EMA (high doesn't touch it),
    #           trigger when the next candle breaks its high.
    if p_hi < e_p and c_hi > p_hi:
        entry, risk = p_hi, p_hi - p_lo
        if risk <= 0:
            return None
        return Signal(
            "FIVE_EMA", "CE", "ATM", sl_spot=p_lo, target_spot=entry + FIVE_EMA_RR * risk,
            entry_remarks=(f"5-min signal candle closed fully below the 5 EMA "
                           f"(high {p_hi:.1f} < EMA {e_p:.1f}); price then broke its high "
                           f"{p_hi:.1f} — exhaustion-reversal long. Bought ATM CE."),
            entry_logic=("Power-of-Stocks 5 EMA long: a candle with its HIGH below the "
                         "5 EMA is the alert candle; enter on a break of that candle's high."),
            sl_logic=f"Stop = alert-candle low {p_lo:.1f} (risk {risk:.1f} pts).",
            exit_logic=f"Target = 1:{FIVE_EMA_RR:g} → {entry + FIVE_EMA_RR * risk:.1f}.")

    # SELL (PE): signal candle entirely ABOVE the 5 EMA (low doesn't touch it),
    #            trigger when the next candle breaks its low.
    if p_lo > e_p and c_lo < p_lo:
        entry, risk = p_lo, p_hi - p_lo
        if risk <= 0:
            return None
        return Signal(
            "FIVE_EMA", "PE", "ATM", sl_spot=p_hi, target_spot=entry - FIVE_EMA_RR * risk,
            entry_remarks=(f"5-min signal candle closed fully above the 5 EMA "
                           f"(low {p_lo:.1f} > EMA {e_p:.1f}); price then broke its low "
                           f"{p_lo:.1f} — exhaustion-reversal short. Bought ATM PE."),
            entry_logic=("Power-of-Stocks 5 EMA short: a candle with its LOW above the "
                         "5 EMA is the alert candle; enter on a break of that candle's low."),
            sl_logic=f"Stop = alert-candle high {p_hi:.1f} (risk {risk:.1f} pts).",
            exit_logic=f"Target = 1:{FIVE_EMA_RR:g} → {entry - FIVE_EMA_RR * risk:.1f}.")
    return None


# ── 3. Expiry-day Gamma Scalp ──────────────────────────────────────────────────
def evaluate_expiry_gamma(ctx: Context) -> Optional[Signal]:
    if not ctx.is_expiry:
        return None
    hi, lo = _opening_range(ctx.idx5, ctx.now)
    if hi is None:
        return None
    vwap = _vwap(_today_session(ctx.fut5, ctx.now))
    if vwap is None:
        return None
    # need a fresh volume-backed breakout candle on the futures
    fut = _today_session(ctx.fut5, ctx.now)
    if fut is None or len(fut) < 4:
        return None
    last_vol = float(fut["volume"].iloc[-1])
    avg_vol = float(fut["volume"].iloc[:-1].mean())
    vol_break = last_vol > avg_vol
    fut_last = float(fut["close"].iloc[-1])

    if ctx.spot > hi and fut_last > vwap and vol_break:
        return Signal(
            "EXPIRY_GAMMA", "CE", "ATM", sl_prem_pct=GAMMA_SL_PCT,
            target_prem_pts=GAMMA_TARGET_PTS, time_exit_min=GAMMA_TIME_EXIT_MIN,
            entry_remarks=("Expiry day: spot broke above the opening range with futures "
                           f"above VWAP ({vwap:.1f}) on rising volume — high-gamma momentum "
                           "scalp. Bought ATM CE (cheap premium, fast delta)."),
            entry_logic=("Expiry gamma long: volume-backed break above opening-range high "
                         "while futures trade above session VWAP."),
            sl_logic=f"Hard stop at {GAMMA_SL_PCT*100:.0f}% of premium paid (gamma cuts both ways).",
            exit_logic=(f"Book +{GAMMA_TARGET_PTS:g} premium points, else flat within "
                        f"{GAMMA_TIME_EXIT_MIN} min — never hold expiry theta."))
    if ctx.spot < lo and fut_last < vwap and vol_break:
        return Signal(
            "EXPIRY_GAMMA", "PE", "ATM", sl_prem_pct=GAMMA_SL_PCT,
            target_prem_pts=GAMMA_TARGET_PTS, time_exit_min=GAMMA_TIME_EXIT_MIN,
            entry_remarks=("Expiry day: spot broke below the opening range with futures "
                           f"below VWAP ({vwap:.1f}) on rising volume — high-gamma momentum "
                           "scalp. Bought ATM PE."),
            entry_logic=("Expiry gamma short: volume-backed break below opening-range low "
                         "while futures trade below session VWAP."),
            sl_logic=f"Hard stop at {GAMMA_SL_PCT*100:.0f}% of premium paid.",
            exit_logic=(f"Book +{GAMMA_TARGET_PTS:g} premium points, else flat within "
                        f"{GAMMA_TIME_EXIT_MIN} min."))
    return None


# ── 4. Open-Interest + Opening-Gap directional ─────────────────────────────────
def evaluate_oi_gap(ctx: Context) -> Optional[Signal]:
    hi, lo = _opening_range(ctx.idx5, ctx.now)
    if hi is None:
        return None
    gap = ctx.today_open - ctx.prev_close
    gap_pct = gap / ctx.prev_close * 100 if ctx.prev_close else 0
    if abs(gap_pct) < 0.15:               # need a meaningful gap
        return None
    oi = ctx.oi or {}
    call_res = oi.get("call_res")         # highest Call-OI strike = resistance
    put_sup = oi.get("put_sup")           # highest Put-OI strike = support

    # Gap-UP + spot holding above Put-OI support + break of opening-range high -> CE
    if gap_pct > 0 and ctx.spot > hi and (put_sup is None or ctx.spot > put_sup):
        tgt = call_res if (call_res and call_res > ctx.spot) else hi + (hi - lo)
        return Signal(
            "OI_GAP", "CE", "ATM", sl_spot=lo, target_spot=float(tgt),
            entry_remarks=(f"Gap-UP {gap_pct:+.2f}%; spot {ctx.spot:.1f} holding above "
                           f"Put-OI support {put_sup} and broke opening-range high {hi:.1f} "
                           f"— OI-backed bullish continuation. Bought ATM CE."),
            entry_logic=("OI+Gap long: gap-up open, spot above highest-Put-OI support, "
                         "enter CE on break of opening-range high."),
            sl_logic=f"Stop below opening-range low {lo:.1f} (loss of intraday support).",
            exit_logic=(f"Target = highest-Call-OI resistance {call_res} "
                        f"(or measured range move)." if call_res else
                        "Target = 1× opening-range projection."))
    # Gap-DOWN + spot below Call-OI resistance + break of opening-range low -> PE
    if gap_pct < 0 and ctx.spot < lo and (call_res is None or ctx.spot < call_res):
        tgt = put_sup if (put_sup and put_sup < ctx.spot) else lo - (hi - lo)
        return Signal(
            "OI_GAP", "PE", "ATM", sl_spot=hi, target_spot=float(tgt),
            entry_remarks=(f"Gap-DOWN {gap_pct:+.2f}%; spot {ctx.spot:.1f} below "
                           f"Call-OI resistance {call_res} and broke opening-range low {lo:.1f} "
                           f"— OI-backed bearish continuation. Bought ATM PE."),
            entry_logic=("OI+Gap short: gap-down open, spot below highest-Call-OI resistance, "
                         "enter PE on break of opening-range low."),
            sl_logic=f"Stop above opening-range high {hi:.1f}.",
            exit_logic=(f"Target = highest-Put-OI support {put_sup} (or measured range move)."
                        if put_sup else "Target = 1× opening-range projection."))
    return None


# registry: live_trade iterates this
STRATEGIES = {
    "ORB": evaluate_orb,
    "FIVE_EMA": evaluate_five_ema,
    "EXPIRY_GAMMA": evaluate_expiry_gamma,
    "OI_GAP": evaluate_oi_gap,
}
