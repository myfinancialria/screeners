"""
Pattern + S/R + trendline scanner over FYERS data.

Single stock (detailed + chart):
    python3 analyze.py NSE:SBIN-EQ --chart
    python3 analyze.py NSE:RELIANCE-EQ --resolution 15 --days 30 --chart

Scan a universe and rank tradable setups:
    python3 analyze.py --universe nifty50 --chart
    python3 analyze.py --universe nifty50 --side bullish --min-score 60 --top 15
    python3 analyze.py --symbols NSE:SBIN-EQ,NSE:INFY-EQ --chart

Outputs a ranked table, a CSV under outputs/, and (with --chart) annotated
PNGs under charts/. NOTHING is traded — this only finds setups to review.
"""
import argparse
import csv
import os
from datetime import datetime

from history import get_history
from signals import analyze_df
from universe import resolve_universe

OUT_DIR = os.path.join(os.path.dirname(__file__), "outputs")
CHART_DIR = os.path.join(os.path.dirname(__file__), "charts")


def scan_symbol(symbol, resolution, days):
    df = get_history(symbol, resolution=resolution, days=days)
    if df.empty:
        return {"symbol": symbol, "ok": False, "reason": "no data"}
    return analyze_df(df, symbol)


def print_detail(a):
    if not a.get("ok"):
        print(f"{a['symbol']}: {a.get('reason')}")
        return
    print(f"\n=== {a['symbol']} — {a['direction']} (score {a['score']}) ===")
    print(f"  close {a['close']} | RSI {a['rsi']} | ADX {a['adx']} | "
          f"vol {a['vol_ratio']}x | ATR% {a['atr_pct']} | {a['structure']}")
    print(f"  patterns: {a['patterns']}")
    print(f"  nearest support: {a['near_support']}  resistance: {a['near_resistance']}")
    print("  confirmations:")
    for c in a["confirmations"]:
        print(f"    - {c}")
    if a.get("plan"):
        p = a["plan"]
        print(f"  trade plan: entry {p['entry']} | SL {p['stop_loss']} | "
              f"T1 {p['target1']} | T2 {p['target2']} | R:R {p['risk_reward']}")


def print_table(rows):
    print(f"\n{'SYMBOL':<22}{'DIR':<9}{'SCORE':>6}{'CLOSE':>10}{'RSI':>6}"
          f"{'VOL':>6}{'PATTERNS':<26}")
    print("-" * 92)
    for a in rows:
        print(f"{a['symbol']:<22}{a['direction']:<9}{a['score']:>6}{a['close']:>10}"
              f"{a['rsi']:>6}{a['vol_ratio']:>6}  {a['patterns'][:24]:<24}")


def write_csv(rows, path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    cols = ["symbol", "direction", "score", "close", "rsi", "adx", "vol_ratio",
            "atr_pct", "structure", "patterns", "near_support", "near_resistance"]
    plan_cols = ["entry", "stop_loss", "target1", "target2", "risk_reward"]
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(cols + plan_cols + ["confirmations"])
        for a in rows:
            p = a.get("plan") or {}
            w.writerow([a.get(c) for c in cols]
                       + [p.get(pc) for pc in plan_cols]
                       + [" | ".join(a.get("confirmations", []))])


def main():
    ap = argparse.ArgumentParser(description="FYERS pattern/S-R/trendline scanner")
    ap.add_argument("symbol", nargs="?", help="single FYERS symbol e.g. NSE:SBIN-EQ")
    ap.add_argument("--universe", help="named universe e.g. nifty50")
    ap.add_argument("--symbols", help="comma-separated FYERS symbols")
    ap.add_argument("--resolution", default="D", help="D, 5, 15, 60 ... (default D)")
    ap.add_argument("--days", type=int, default=300)
    ap.add_argument("--side", choices=["both", "bullish", "bearish"], default="both")
    ap.add_argument("--min-score", type=int, default=50)
    ap.add_argument("--top", type=int, default=20)
    ap.add_argument("--chart", action="store_true", help="save annotated PNG charts")
    ap.add_argument("--out", help="CSV output path")
    args = ap.parse_args()

    # build symbol list
    if args.symbol:
        symbols = [args.symbol]
    elif args.symbols:
        symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
    elif args.universe:
        symbols = resolve_universe(args.universe)
    else:
        ap.error("give a SYMBOL, --symbols, or --universe")

    single = len(symbols) == 1

    results = []
    for i, sym in enumerate(symbols, 1):
        try:
            a = scan_symbol(sym, args.resolution, args.days)
        except Exception as e:
            print(f"[{i}/{len(symbols)}] {sym}: ERROR {e}")
            continue
        if not single:
            print(f"[{i}/{len(symbols)}] {sym}: "
                  f"{a.get('direction', a.get('reason'))} {a.get('score','')}")
        if a.get("ok"):
            results.append(a)

    # filter + rank
    want = {"both": {"BULLISH", "BEARISH"},
            "bullish": {"BULLISH"}, "bearish": {"BEARISH"}}[args.side]
    ranked = sorted(
        [a for a in results if a["direction"] in want and a["score"] >= args.min_score],
        key=lambda a: a["score"], reverse=True,
    )[: args.top]

    if single:
        print_detail(results[0] if results else {"symbol": symbols[0], "ok": False, "reason": "no data"})
        ranked = results  # chart the one we have
    else:
        if ranked:
            print_table(ranked)
        else:
            print("\nNo setups passed the filters.")

    # CSV
    if ranked:
        stamp = datetime.now().strftime("%Y%m%d_%H%M")
        out = args.out or os.path.join(OUT_DIR, f"signals_{args.resolution}_{stamp}.csv")
        write_csv(ranked, out)
        print(f"\nCSV: {out}")

    # charts
    if args.chart and ranked:
        from chart import draw
        os.makedirs(CHART_DIR, exist_ok=True)
        for a in ranked:
            safe = a["symbol"].replace(":", "_").replace("&", "")
            path = os.path.join(CHART_DIR, f"{safe}_{args.resolution}.png")
            try:
                draw(a, path)
                print(f"chart: {path}")
            except Exception as e:
                print(f"chart failed for {a['symbol']}: {e}")


if __name__ == "__main__":
    main()
