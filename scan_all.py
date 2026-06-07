"""
Scan the ENTIRE NSE (or any universe) concurrently and surface bullish setups
early — breakouts happening now, pre-breakouts coiling under highs, and
uptrend pullbacks. Liquidity-filtered, rate-limited, ranked.

    python3 scan_all.py                         # full NSE, all bullish setups
    python3 scan_all.py --setup pre_breakout    # only "about to break out"
    python3 scan_all.py --setup breakout --chart --top 30
    python3 scan_all.py --universe nifty50      # smaller test universe
    python3 scan_all.py --min-value 20 --min-price 100 --workers 10 --rps 8

NOTHING is traded. Output: ranked table + CSV in outputs/ (+ charts with --chart).
"""
import argparse
import csv
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

from history import get_history
from ratelimit import limiter
from signals import analyze_df
from universe import resolve_universe

OUT_DIR = os.path.join(os.path.dirname(__file__), "outputs")
CHART_DIR = os.path.join(os.path.dirname(__file__), "charts")

SETUP_PRIORITY = {"BREAKOUT": 3, "PRE_BREAKOUT": 2, "PULLBACK": 1, "TREND": 0, "": 0}
SETUP_FILTERS = {
    "all": lambda a: a["early_bullish"] or a["direction"] == "BULLISH",
    "breakout": lambda a: a["bull_setup"] == "BREAKOUT",
    "pre_breakout": lambda a: a["bull_setup"] == "PRE_BREAKOUT",
    "pullback": lambda a: a["bull_setup"] == "PULLBACK",
    "bullish": lambda a: a["direction"] == "BULLISH",
}


def scan_one(symbol, resolution, days):
    try:
        df = get_history(symbol, resolution=resolution, days=days)
        if df.empty or len(df) < 60:
            return None
        a = analyze_df(df, symbol)
        return a if a.get("ok") else None
    except Exception:
        return None


def run_scan(symbols, resolution="D", days=300, setup="all", min_value=5.0,
             min_price=50.0, min_score=0, top=50, workers=10, rps=8.0, progress=True):
    """Scan symbols concurrently; return dict with results/rows/top/stats."""
    limiter.configure(rps)
    t0 = time.monotonic()
    results, done = [], 0
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(scan_one, s, resolution, days): s for s in symbols}
        for fut in as_completed(futs):
            done += 1
            a = fut.result()
            if a:
                results.append(a)
            if progress and (done % 100 == 0 or done == len(symbols)):
                rate = done / max(time.monotonic() - t0, 1e-9)
                print(f"  {done}/{len(symbols)}  ({rate:.1f}/s)  hits so far: {len(results)}")

    keep = SETUP_FILTERS[setup]
    rows = [
        a for a in results
        if a["avg_value_cr"] >= min_value and a["close"] >= min_price
        and a["score"] >= min_score and keep(a)
    ]
    rows.sort(key=lambda a: (SETUP_PRIORITY.get(a["bull_setup"], 0), a["score"]), reverse=True)
    return {"results": results, "rows": rows, "top": rows[:top],
            "total": len(symbols), "valid": len(results)}


def write_csv(rows, path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    cols = ["symbol", "bull_setup", "direction", "score", "close", "dist_to_high_pct",
            "rsi", "adx", "vol_ratio", "avg_value_cr", "squeeze", "structure",
            "patterns", "near_support", "near_resistance"]
    plan_cols = ["entry", "stop_loss", "target1", "target2", "risk_reward"]
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(cols + plan_cols + ["confirmations"])
        for a in rows:
            p = a.get("plan") or {}
            w.writerow([a.get(c) for c in cols] + [p.get(pc) for pc in plan_cols]
                       + [" | ".join(a.get("confirmations", []))])
    return path


def main():
    ap = argparse.ArgumentParser(description="Full-NSE bullish/breakout scanner")
    ap.add_argument("--universe", default="nse_all", help="nse_all (default) | nifty50")
    ap.add_argument("--symbols", help="comma-separated symbols (overrides universe)")
    ap.add_argument("--resolution", default="D")
    ap.add_argument("--days", type=int, default=300)
    ap.add_argument("--setup", choices=list(SETUP_FILTERS), default="all")
    ap.add_argument("--min-value", type=float, default=5.0, help="min avg traded value (₹ cr)")
    ap.add_argument("--min-price", type=float, default=50.0)
    ap.add_argument("--min-score", type=int, default=0)
    ap.add_argument("--top", type=int, default=50)
    ap.add_argument("--workers", type=int, default=10)
    ap.add_argument("--rps", type=float, default=8.0, help="API requests/sec cap")
    ap.add_argument("--chart", action="store_true")
    ap.add_argument("--out", help="CSV output path")
    args = ap.parse_args()

    if args.symbols:
        symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
    else:
        symbols = resolve_universe(args.universe)

    print(f"Scanning {len(symbols)} symbols  (res={args.resolution}, "
          f"{args.workers} workers, {args.rps}/s)...")

    res = run_scan(symbols, resolution=args.resolution, days=args.days, setup=args.setup,
                   min_value=args.min_value, min_price=args.min_price,
                   min_score=args.min_score, top=args.top, workers=args.workers, rps=args.rps)
    rows, top = res["rows"], res["top"]

    # report
    print(f"\nScanned {res['valid']} valid / {res['total']} | "
          f"{len(rows)} passed filters | showing top {len(top)}\n")
    print(f"{'SYMBOL':<22}{'SETUP':<14}{'DIR':<9}{'SCORE':>6}{'CLOSE':>10}"
          f"{'%toHigh':>8}{'RSI':>6}{'VOL':>6}{'₹Cr':>8}  PATTERNS")
    print("-" * 104)
    for a in top:
        print(f"{a['symbol']:<22}{a['bull_setup']:<14}{a['direction']:<9}{a['score']:>6}"
              f"{a['close']:>10}{a['dist_to_high_pct']:>8}{a['rsi']:>6}"
              f"{a['vol_ratio']:>6}{a['avg_value_cr']:>8}  {a['patterns'][:20]}")

    # CSV
    if top:
        stamp = datetime.now().strftime("%Y%m%d_%H%M")
        out = args.out or os.path.join(OUT_DIR, f"nse_bullish_{args.setup}_{stamp}.csv")
        write_csv(top, out)
        print(f"\nCSV: {out}")

    # charts
    if args.chart and top:
        from chart import draw
        os.makedirs(CHART_DIR, exist_ok=True)
        for a in top:
            safe = a["symbol"].replace(":", "_").replace("&", "")
            path = os.path.join(CHART_DIR, f"{safe}_{args.resolution}.png")
            try:
                draw(a, path)
            except Exception as e:
                print(f"chart failed {a['symbol']}: {e}")
        print(f"charts: {len(top)} saved to {CHART_DIR}/")


if __name__ == "__main__":
    main()
