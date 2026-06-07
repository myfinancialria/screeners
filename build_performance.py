"""
Build the performance dashboard: runs the backtest, draws the equity curve,
and renders site/performance.html + site/equity.png + site/trades.csv.

    python3 build_performance.py --universe nifty50      # quick test
    python3 build_performance.py --universe nse_all      # full (used in CI)
"""
import argparse
import csv
import os
from datetime import datetime, timedelta, timezone

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

from backtest import run_backtest
from universe import resolve_universe

SITE_DIR = os.path.join(os.path.dirname(__file__), "site")
IST = timezone(timedelta(hours=5, minutes=30))

CSS = """
*{box-sizing:border-box}body{margin:0;background:#0f1620;color:#e6e9ee;
font-family:-apple-system,Segoe UI,Roboto,Arial,sans-serif;line-height:1.5}
.wrap{max-width:1180px;margin:0 auto;padding:24px 16px 60px}
h1{font-size:24px;margin:0 0 4px}.sub{color:#8b97a7;font-size:14px;margin:0 0 18px}
a{color:#5aa9ff}
.cards{display:flex;gap:12px;flex-wrap:wrap;margin:16px 0}
.card{background:#19222e;border:1px solid #263240;border-radius:10px;padding:12px 16px;flex:1;min-width:130px}
.card .n{font-size:22px;font-weight:700}.card .l{color:#8b97a7;font-size:12px}
.pos{color:#5fd39a}.neg{color:#e07a6a}
img{max-width:100%;border:1px solid #263240;border-radius:10px;margin:8px 0 18px}
table{width:100%;border-collapse:collapse;background:#141c26;border:1px solid #263240;
border-radius:8px;overflow:hidden;font-size:12px}
th{background:#1f2b3a;text-align:left;padding:8px;color:#c7d0db;white-space:nowrap}
td{padding:7px 8px;border-top:1px solid #202a36;vertical-align:top}
tr:hover td{background:#18222e}.r{text-align:right}.c{text-align:center}
.sym{font-weight:700;color:#fff}.note{color:#9aa6b3;font-size:11px;white-space:normal;min-width:240px}
.tag{padding:1px 7px;border-radius:10px;font-size:10px;font-weight:700;color:#0f1620}
.foot{color:#6b7686;font-size:12px;margin-top:26px}
"""


def draw_equity(equity, capital, path):
    s = pd.Series(equity).sort_index()
    fig, ax = plt.subplots(figsize=(11, 4.2))
    ax.plot(s.index, s.values, color="#5fd39a", lw=1.6)
    ax.axhline(capital, color="#6b7686", ls="--", lw=0.9, label="Start ₹50L")
    peak = s.cummax()
    ax.fill_between(s.index, s.values, peak.values, where=(s < peak),
                    color="#e07a6a", alpha=0.15, label="Drawdown")
    ax.set_title("Portfolio Equity Curve", color="#e6e9ee")
    ax.legend(loc="upper left", fontsize=8)
    ax.grid(alpha=0.15)
    fig.autofmt_xdate()
    for sp in ax.spines.values():
        sp.set_color("#263240")
    ax.tick_params(colors="#8b97a7")
    fig.patch.set_facecolor("#0f1620")
    ax.set_facecolor("#141c26")
    ax.yaxis.label.set_color("#8b97a7")
    fig.savefig(path, dpi=120, bbox_inches="tight", facecolor="#0f1620")
    plt.close(fig)


def _color(v):
    return "pos" if v > 0 else ("neg" if v < 0 else "")


def trade_rows(trades, limit=200):
    out = []
    for t in sorted(trades, key=lambda x: x["entry_date"], reverse=True)[:limit]:
        sym = t["symbol"].replace("NSE:", "").replace("-EQ", "")
        ed = t["entry_date"].strftime("%d-%b-%y")
        xd = t["exit_date"].strftime("%d-%b-%y") if t["exit_date"] else "—"
        tag = "#5fd39a" if t["status"] == "CLOSED" and t["pnl"] > 0 else (
            "#e07a6a" if t["status"] == "CLOSED" else "#e6b35a")
        note = (f"<b>Entry:</b> {t['reason']}. "
                f"<b>Target:</b> {t['target']} (+2R — trim 25%, trail the rest). "
                f"<b>Exit:</b> {t['exit_reason']}.")
        out.append(
            "<tr>"
            f"<td class='sym'>{sym}</td>"
            f"<td><span class='tag' style='background:{tag}'>{t['setup']}</span></td>"
            f"<td>{ed}</td><td class='r'>{t['entry']}</td><td class='r'>{t['qty']}</td>"
            f"<td class='r'>{t['sl']}</td><td class='r'>{t['target']}</td>"
            f"<td>{xd}</td><td class='r'>{t['exit_price']}</td>"
            f"<td class='r {_color(t['pnl'])}'>{t['pnl']:,.0f}</td>"
            f"<td class='r {_color(t['pnl'])}'>{t['ret_pct']}%</td>"
            f"<td class='c'>{t['r_multiple']}</td><td class='c'>{t['bars_held']}</td>"
            f"<td class='note'>{note}</td>"
            "</tr>")
    return "".join(out)


def write_trades_csv(trades, path):
    cols = ["symbol", "setup", "entry_date", "entry", "qty", "value", "sl", "target",
            "risk_amt", "exit_date", "exit_price", "exit_reason", "status", "pnl",
            "ret_pct", "r_multiple", "bars_held", "reason"]
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(cols)
        for t in sorted(trades, key=lambda x: x["entry_date"]):
            row = dict(t)
            row["entry_date"] = t["entry_date"].strftime("%Y-%m-%d")
            row["exit_date"] = t["exit_date"].strftime("%Y-%m-%d") if t["exit_date"] else ""
            w.writerow([row.get(c) for c in cols])


def build_html(res, when):
    s, p = res["stats"], res["params"]
    rc = _color(s["total_return_pct"])

    def card(n, label, cls=""):
        return f"<div class='card'><div class='n {cls}'>{n}</div><div class='l'>{label}</div></div>"

    cards = "".join([
        card(f"₹{s['end_equity']:,.0f}", "Equity (start ₹50,00,000)"),
        card(f"{s['total_return_pct']:+.2f}%", "Total return", rc),
        card(f"{s['cagr_pct']:+.2f}%", "CAGR", _color(s['cagr_pct'])),
        card(f"{s['max_drawdown_pct']}%", "Max drawdown", "neg"),
        card(f"{s['win_rate_pct']}%", "Win rate"),
        card(f"{s['profit_factor']}", "Profit factor"),
        card(f"{s['avg_r']}", "Avg R"),
        card(f"{s['total_trades']}", f"Trades ({s['open_trades']} open)"),
    ])
    head = ("<th>Stock</th><th>Setup</th><th>Entry date</th><th class='r'>Entry</th>"
            "<th class='r'>Qty</th><th class='r'>SL</th><th class='r'>Target</th>"
            "<th>Exit date</th><th class='r'>Exit</th><th class='r'>P&L</th>"
            "<th class='r'>Ret</th><th class='c'>R</th><th class='c'>Days</th><th>Notes</th>")
    shown = min(200, len(res["trades"]))
    return f"""<!doctype html><html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Screener Performance — {when}</title><style>{CSS}</style></head>
<body><div class="wrap">
<h1>📊 Screener Performance (Backtest)</h1>
<p class="sub">{s['period']} · {p['universe_size']} stocks · updated {when} IST ·
<a href="index.html">← back to screener</a> · <a href="trades.csv">download all trades</a></p>
<div class="cards">{cards}</div>
<img src="equity.png" alt="Equity curve">
<h3 style="margin:18px 0 6px">Rules</h3>
<p class="sub">Capital ₹{p['capital']:,.0f} · max {p['max_positions']} positions ·
max {p['max_alloc_pct']}% equity/stock · max ₹{p['max_risk']:,.0f} risk/stock ·
SL = entry − {p['atr_mult_sl']}×ATR. At the +{p['rr']}R target, <b>trim
{int(p['trim_pct']*100)}%</b>, move the rest to breakeven and <b>let the winner run</b>
with a {p['atr_trail']}×ATR Chandelier trail — cutting on a reversal (close &lt; EMA20 or
a bearish reversal candle). {p['max_hold']}-day time stop · {p['cost_pct']}% cost/side.
Entries on BREAKOUT &amp; PULLBACK setups at signal-day close.</p>
<h3 style="margin:18px 0 6px">Trades <span style="color:#6b7686">(latest {shown} of {len(res['trades'])})</span></h3>
<table><thead><tr>{head}</tr></thead><tbody>{trade_rows(res['trades'])}</tbody></table>
<p class="foot">Backtest with no look-ahead (point-in-time signals). Past performance
is not indicative of future results. Educational use only — not investment advice.</p>
</div></body></html>"""


def main():
    ap = argparse.ArgumentParser(description="Build the performance dashboard")
    ap.add_argument("--universe", default="nse_all")
    ap.add_argument("--days", type=int, default=500)
    args = ap.parse_args()

    symbols = resolve_universe(args.universe)
    print(f"Backtesting {len(symbols)} symbols over {args.days} days...")
    res = run_backtest(symbols, days=args.days)

    os.makedirs(SITE_DIR, exist_ok=True)
    draw_equity(res["equity"], res["params"]["capital"], os.path.join(SITE_DIR, "equity.png"))
    write_trades_csv(res["trades"], os.path.join(SITE_DIR, "trades.csv"))
    when = datetime.now(IST).strftime("%d %b %Y, %I:%M %p")
    with open(os.path.join(SITE_DIR, "performance.html"), "w") as f:
        f.write(build_html(res, when))

    s = res["stats"]
    print(f"Done. Equity ₹{s['end_equity']:,.0f} | return {s['total_return_pct']}% | "
          f"win {s['win_rate_pct']}% | trades {s['total_trades']}")
    print(f"Wrote {SITE_DIR}/performance.html, equity.png, trades.csv")


if __name__ == "__main__":
    main()
