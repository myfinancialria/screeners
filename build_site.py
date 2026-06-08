"""
Generate the screener page (site/screener.html + site/data.csv) from a fresh
NSE scan. The GitHub Action runs this daily and publishes site/ to Pages.

    python3 build_site.py                  # full NSE -> site/
    python3 build_site.py --universe nifty50   # quick local test
"""
import argparse
import os
from datetime import datetime, timedelta, timezone

from scan_all import run_scan, write_csv
from universe import resolve_universe

SITE_DIR = os.path.join(os.path.dirname(__file__), "site")
IST = timezone(timedelta(hours=5, minutes=30))

SETUP_LABEL = {
    "BREAKOUT": ("🚀 Breaking out now", "#27ae60"),
    "PRE_BREAKOUT": ("⏳ Pre-breakout — coiling under highs", "#e67e22"),
    "PULLBACK": ("↩️ Uptrend pullback", "#2980b9"),
}
SETUP_ORDER = ["PRE_BREAKOUT", "BREAKOUT", "PULLBACK"]

CSS = """
*{box-sizing:border-box} body{margin:0;background:#0f1620;color:#e6e9ee;
font-family:-apple-system,Segoe UI,Roboto,Arial,sans-serif;line-height:1.5}
.wrap{max-width:1100px;margin:0 auto;padding:24px 16px 60px}
h1{font-size:24px;margin:0 0 4px} .sub{color:#8b97a7;font-size:14px;margin:0 0 20px}
.cards{display:flex;gap:12px;flex-wrap:wrap;margin:16px 0 8px}
.card{background:#19222e;border:1px solid #263240;border-radius:10px;padding:12px 16px;flex:1;min-width:150px}
.card .n{font-size:26px;font-weight:700} .card .l{color:#8b97a7;font-size:13px}
h2{font-size:17px;margin:26px 0 8px;padding-left:10px;border-left:4px solid}
table{width:100%;border-collapse:collapse;background:#141c26;border:1px solid #263240;
border-radius:8px;overflow:hidden;font-size:13px}
th{background:#1f2b3a;text-align:left;padding:9px 10px;color:#c7d0db;font-weight:600;white-space:nowrap}
td{padding:8px 10px;border-top:1px solid #202a36;white-space:nowrap}
tr:hover td{background:#18222e}
.sym{font-weight:700;color:#fff} .r{text-align:right} .c{text-align:center}
.sl{color:#e07a6a} .tg{color:#5fd39a} .pat{color:#8b97a7;font-size:11px;white-space:normal}
a{color:#5aa9ff} .foot{color:#6b7686;font-size:12px;margin-top:30px}
.badge{display:inline-block;padding:2px 8px;border-radius:20px;font-size:11px;font-weight:600;color:#0f1620}
"""


def _rows(rows):
    out = []
    for a in rows:
        p = a.get("plan") or {}
        sym = a["symbol"].replace("NSE:", "").replace("-EQ", "")
        out.append(
            "<tr>"
            f"<td class='sym'>{sym}</td>"
            f"<td class='c'>{a['score']}</td>"
            f"<td class='r'>{a['close']}</td>"
            f"<td class='r'>{a['dist_to_high_pct']}%</td>"
            f"<td class='c'>{a['rsi']}</td>"
            f"<td class='c'>{a['vol_ratio']}x</td>"
            f"<td class='r'>{a['avg_value_cr']}</td>"
            f"<td class='r'>{p.get('entry','-')}</td>"
            f"<td class='r sl'>{p.get('stop_loss','-')}</td>"
            f"<td class='r tg'>{p.get('target1','-')}</td>"
            f"<td class='c'>{p.get('risk_reward','-')}</td>"
            f"<td class='pat'>{a['patterns'] if a['patterns']!='-' else ''}</td>"
            "</tr>"
        )
    return "".join(out)


def build_html(top, total, valid, when):
    counts = {s: len([a for a in top if a["bull_setup"] == s]) for s in SETUP_ORDER}
    cards = "".join(
        f"<div class='card'><div class='n'>{counts[s]}</div>"
        f"<div class='l'>{SETUP_LABEL[s][0]}</div></div>" for s in SETUP_ORDER
    )
    head = ("<th>Stock</th><th class='c'>Score</th><th class='r'>Close</th>"
            "<th class='r'>%toHigh</th><th class='c'>RSI</th><th class='c'>Vol</th>"
            "<th class='r'>₹Cr</th><th class='r'>Entry</th><th class='r'>SL</th>"
            "<th class='r'>T1</th><th class='c'>R:R</th><th>Patterns</th>")

    def rr_of(a):
        return (a.get("plan") or {}).get("risk_reward") or 0

    def setup_sections(subset):
        out = ""
        for s in SETUP_ORDER:
            rows = [a for a in subset if a["bull_setup"] == s]
            if not rows:
                continue
            label, color = SETUP_LABEL[s]
            out += (f"<h3 style='border-color:{color};border-left:4px solid;padding-left:10px'>"
                    f"{label} <span style='color:#6b7686'>({len(rows)})</span></h3>"
                    f"<table><thead><tr>{head}</tr></thead><tbody>{_rows(rows)}</tbody></table>")
        return out

    MIN_RR = 1.5
    tradable = [a for a in top if rr_of(a) >= MIN_RR]
    watch = [a for a in top if rr_of(a) < MIN_RR]
    sections = (
        f"<h2 style='color:#5fd39a'>✅ Tradable — R:R ≥ {MIN_RR} "
        f"<span style='color:#6b7686'>({len(tradable)})</span></h2>"
        + (setup_sections(tradable) or "<p>None today.</p>")
        + f"<h2 style='color:#e6b35a;margin-top:26px'>👀 Watchlist — R:R &lt; {MIN_RR}, don't trade yet "
          f"<span style='color:#6b7686'>({len(watch)})</span></h2>"
        + (setup_sections(watch) or "<p>None today.</p>")
    )
    if not top:
        sections = "<p>No bullish setups passed the filters today.</p>"

    return f"""<!doctype html><html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>NSE Bullish Screener — {when}</title><style>{CSS}</style></head>
<body><div class="wrap">
<h1>📈 NSE Bullish Screener</h1>
<p class="sub">Updated {when} IST · scanned {valid} liquid of {total} NSE stocks ·
<a href="index.html">🏠 Home</a> · <a href="data.csv">download CSV</a> · <a href="performance.html">📊 performance dashboard →</a> · <a href="journal.html">🤖 strategy paper-trading journal →</a></p>
<div class="cards">{cards}</div>
{sections}
<p class="foot">Auto-generated by a FYERS-powered scanner. Only setups with
reward:risk ≥ 1.5 (to the next resistance) are marked tradable; the rest are
watchlist-only. <b>For educational use only — not investment advice.</b>
Verify charts and do your own research before trading.</p>
</div></body></html>"""


def main():
    ap = argparse.ArgumentParser(description="Build the static screener website")
    ap.add_argument("--universe", default="nse_all")
    ap.add_argument("--setup", default="all")
    ap.add_argument("--min-value", type=float, default=10.0)
    ap.add_argument("--min-price", type=float, default=50.0)
    ap.add_argument("--top", type=int, default=80)
    ap.add_argument("--workers", type=int, default=10)
    ap.add_argument("--rps", type=float, default=8.0)
    args = ap.parse_args()

    symbols = resolve_universe(args.universe)
    print(f"Scanning {len(symbols)} symbols for the website...")
    res = run_scan(symbols, setup=args.setup, min_value=args.min_value,
                   min_price=args.min_price, top=args.top,
                   workers=args.workers, rps=args.rps)
    top = res["top"]
    when = datetime.now(IST).strftime("%d %b %Y, %I:%M %p")

    os.makedirs(SITE_DIR, exist_ok=True)
    with open(os.path.join(SITE_DIR, "screener.html"), "w") as f:
        f.write(build_html(top, res["total"], res["valid"], when))
    if top:
        write_csv(top, os.path.join(SITE_DIR, "data.csv"))
    print(f"Site written to {SITE_DIR}/ ({len(top)} setups)")


if __name__ == "__main__":
    main()
