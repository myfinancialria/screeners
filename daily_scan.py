"""
Daily automated NSE bullish scan -> emailed HTML report.

    python3 daily_scan.py                 # scan full NSE, email the report
    python3 daily_scan.py --no-send       # build report to outputs/preview.html (no email)
    python3 daily_scan.py --universe nifty50 --no-send   # quick test

Intended to be run by the scheduler after market close. If the FYERS token is
expired it emails a reminder to re-run auth.py instead of failing silently.
"""
import argparse
import os
from datetime import datetime

from fyers_data import ltp
from scan_all import run_scan, write_csv
from universe import resolve_universe

OUT_DIR = os.path.join(os.path.dirname(__file__), "outputs")

SETUP_LABEL = {
    "BREAKOUT": "🚀 Breaking out now",
    "PRE_BREAKOUT": "⏳ Pre-breakout (coiling under highs)",
    "PULLBACK": "↩️ Uptrend pullback",
}
SETUP_ORDER = ["PRE_BREAKOUT", "BREAKOUT", "PULLBACK"]  # early signals first


def token_ok() -> bool:
    try:
        q = ltp("NSE:SBIN-EQ")
        return bool(q.get("NSE:SBIN-EQ"))
    except Exception:
        return False


def _row_html(a):
    p = a.get("plan") or {}
    return (
        "<tr>"
        f"<td><b>{a['symbol'].replace('NSE:','').replace('-EQ','')}</b></td>"
        f"<td style='text-align:center'>{a['score']}</td>"
        f"<td style='text-align:right'>{a['close']}</td>"
        f"<td style='text-align:right'>{a['dist_to_high_pct']}%</td>"
        f"<td style='text-align:center'>{a['rsi']}</td>"
        f"<td style='text-align:center'>{a['vol_ratio']}x</td>"
        f"<td style='text-align:right'>{a['avg_value_cr']}</td>"
        f"<td style='text-align:right'>{p.get('entry','-')}</td>"
        f"<td style='text-align:right;color:#c0392b'>{p.get('stop_loss','-')}</td>"
        f"<td style='text-align:right;color:#27ae60'>{p.get('target1','-')}</td>"
        f"<td style='text-align:center'>{p.get('risk_reward','-')}</td>"
        f"<td style='font-size:11px;color:#555'>{a['patterns'] if a['patterns']!='-' else ''}</td>"
        "</tr>"
    )


def build_html(top, total, valid, when):
    head = (
        "<th>Stock</th><th>Score</th><th>Close</th><th>%toHigh</th><th>RSI</th>"
        "<th>Vol</th><th>₹Cr</th><th>Entry</th><th>SL</th><th>T1</th><th>R:R</th><th>Patterns</th>"
    )
    groups = ""
    for setup in SETUP_ORDER:
        rows = [a for a in top if a["bull_setup"] == setup]
        if not rows:
            continue
        body = "".join(_row_html(a) for a in rows)
        groups += (
            f"<h3 style='margin:18px 0 6px'>{SETUP_LABEL[setup]} "
            f"<span style='color:#888;font-weight:normal'>({len(rows)})</span></h3>"
            "<table cellpadding='6' cellspacing='0' style='border-collapse:collapse;"
            "width:100%;font-family:Arial,sans-serif;font-size:13px;border:1px solid #ddd'>"
            f"<thead><tr style='background:#1f2d3d;color:#fff'>{head}</tr></thead>"
            f"<tbody>{body}</tbody></table>"
        )
    if not groups:
        groups = "<p>No bullish setups passed the filters today.</p>"

    return (
        "<div style='font-family:Arial,sans-serif;max-width:980px;margin:auto'>"
        f"<h2 style='margin-bottom:0'>NSE Bullish Scan — {when}</h2>"
        f"<p style='color:#666;margin-top:4px'>Scanned {valid} liquid of {total} "
        "NSE stocks. Setups ranked early-first. Not investment advice — review charts "
        "before acting.</p>"
        f"{groups}"
        "<p style='color:#999;font-size:11px;margin-top:20px'>Generated automatically "
        "by your FYERS scanner. Full data attached as CSV.</p></div>"
    )


def main():
    ap = argparse.ArgumentParser(description="Daily NSE bullish scan + email")
    ap.add_argument("--universe", default="nse_all")
    ap.add_argument("--setup", default="all")
    ap.add_argument("--min-value", type=float, default=10.0)
    ap.add_argument("--min-price", type=float, default=50.0)
    ap.add_argument("--top", type=int, default=60)
    ap.add_argument("--workers", type=int, default=10)
    ap.add_argument("--rps", type=float, default=8.0)
    ap.add_argument("--no-send", action="store_true", help="write preview HTML, don't email")
    args = ap.parse_args()

    when = datetime.now().strftime("%d %b %Y, %I:%M %p")

    # token guard
    if not token_ok():
        msg = ("Today's NSE scan could not run because the FYERS access token is "
               "expired. Open Terminal and run:<br><br>"
               "<code>cd /Users/nithin/fyers-connect &amp;&amp; python3 auth.py</code>"
               "<br><br>Then it will work again at the next scheduled run.")
        print("Token invalid.")
        if not args.no_send:
            from notify_email import send_email
            send_email(f"⚠️ FYERS scan skipped — token expired ({when})",
                       f"<div style='font-family:Arial'>{msg}</div>")
        return

    symbols = resolve_universe(args.universe)
    print(f"Scanning {len(symbols)} symbols...")
    res = run_scan(symbols, setup=args.setup, min_value=args.min_value,
                   min_price=args.min_price, top=args.top,
                   workers=args.workers, rps=args.rps)
    top = res["top"]

    html = build_html(top, res["total"], res["valid"], when)

    os.makedirs(OUT_DIR, exist_ok=True)
    csv_path = None
    if top:
        stamp = datetime.now().strftime("%Y%m%d")
        csv_path = write_csv(top, os.path.join(OUT_DIR, f"daily_{stamp}.csv"))

    subject = f"NSE Bullish Scan — {datetime.now():%d %b} — {len(top)} setups"
    if args.no_send:
        preview = os.path.join(OUT_DIR, "preview.html")
        with open(preview, "w") as f:
            f.write(html)
        print(f"Preview written: {preview}\nSubject would be: {subject}")
    else:
        from notify_email import send_email
        send_email(subject, html, attachments=[csv_path] if csv_path else None)
        print(f"Emailed: {subject}")


if __name__ == "__main__":
    main()
