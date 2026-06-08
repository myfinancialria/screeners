#!/usr/bin/env python3
"""
Build the performance + journal report from strategy.db.

    python3 build_journal.py            # -> site/journal.html  +  site/journal.csv

Two tabs:
  • Overall Return — headline P&L, return %, win-rate, profit factor, avg R,
    max drawdown, and a per-strategy breakdown.
  • Detailed Journal — every trade with entry/exit/SL/target levels and the full
    remarks: why it entered, and the entry / stop / exit logic.
"""
from __future__ import annotations

import csv
import datetime as dt
import html
from pathlib import Path

import journal_db as J

SITE = Path(__file__).with_name("site")
SITE.mkdir(exist_ok=True)


def _stats(trades):
    closed = [t for t in trades if t["status"] == "CLOSED"]
    wins = [t for t in closed if (t["pnl"] or 0) > 0]
    losses = [t for t in closed if (t["pnl"] or 0) <= 0]
    gross_win = sum(t["pnl"] for t in wins) if wins else 0.0
    gross_loss = sum(t["pnl"] for t in losses) if losses else 0.0
    net = sum((t["pnl"] or 0) for t in closed)
    rs = [t["r_multiple"] for t in closed if t["r_multiple"] is not None]

    # max drawdown on the realised equity curve (ordered by exit time)
    eq, peak, mdd = 0.0, 0.0, 0.0
    for t in sorted(closed, key=lambda x: x["exit_ts"] or ""):
        eq += t["pnl"] or 0
        peak = max(peak, eq)
        mdd = min(mdd, eq - peak)

    cap = J.get_capital()
    return {
        "capital": cap,
        "n_total": len(trades),
        "n_closed": len(closed),
        "n_open": len([t for t in trades if t["status"] == "OPEN"]),
        "net": net,
        "ret_pct": net / cap * 100 if cap else 0.0,
        "win_rate": len(wins) / len(closed) * 100 if closed else 0.0,
        "pf": (gross_win / abs(gross_loss)) if gross_loss else (float("inf") if gross_win else 0.0),
        "avg_r": sum(rs) / len(rs) if rs else 0.0,
        "max_dd": mdd,
        "gross_win": gross_win,
        "gross_loss": gross_loss,
    }


def _per_strategy(trades):
    by = {}
    for t in trades:
        if t["status"] != "CLOSED":
            continue
        s = by.setdefault(t["strategy"], {"n": 0, "wins": 0, "pnl": 0.0})
        s["n"] += 1
        s["pnl"] += t["pnl"] or 0
        if (t["pnl"] or 0) > 0:
            s["wins"] += 1
    return by


def _fmt(v, nd=2, money=False):
    if v is None:
        return "—"
    if money:
        return f"₹{v:,.0f}"
    if v == float("inf"):
        return "∞"
    return f"{v:,.{nd}f}"


def write_csv(trades, path):
    cols = ["id", "date", "strategy", "underlying", "opt_symbol", "opt_kind", "lots",
            "qty", "status", "entry_ts", "entry_spot", "entry_prem", "sl_prem",
            "target_prem", "sl_spot", "target_spot", "exit_ts", "exit_prem",
            "exit_reason", "pnl", "pnl_pct", "r_multiple", "entry_remarks",
            "entry_logic", "sl_logic", "exit_logic"]
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for t in trades:
            w.writerow(t)


def build():
    J.init()
    trades = J.all_trades()
    st = _stats(trades)
    per = _per_strategy(trades)
    now = dt.datetime.now().strftime("%d-%b-%Y %H:%M")

    # ── Overall Return tab ──
    cards = [
        ("Net P&L", _fmt(st["net"], money=True), st["net"] >= 0),
        ("Return on capital", f"{st['ret_pct']:+.2f}%", st["ret_pct"] >= 0),
        ("Trades (closed)", f"{st['n_closed']}", None),
        ("Open now", f"{st['n_open']}", None),
        ("Win rate", f"{st['win_rate']:.1f}%", st["win_rate"] >= 50),
        ("Profit factor", _fmt(st["pf"]), st["pf"] >= 1),
        ("Avg R multiple", f"{st['avg_r']:+.2f}R", st["avg_r"] >= 0),
        ("Max drawdown", _fmt(st["max_dd"], money=True), False),
    ]
    card_html = "".join(
        f'<div class="card"><div class="cap">{html.escape(lbl)}</div>'
        f'<div class="val {"pos" if good else ("neg" if good is False else "")}">{val}</div></div>'
        for lbl, val, good in cards)

    rows = []
    for s, d in sorted(per.items(), key=lambda x: -x[1]["pnl"]):
        wr = d["wins"] / d["n"] * 100 if d["n"] else 0
        cls = "pos" if d["pnl"] >= 0 else "neg"
        rows.append(f"<tr><td>{html.escape(s)}</td><td>{d['n']}</td><td>{wr:.0f}%</td>"
                    f"<td class='{cls}'>{_fmt(d['pnl'], money=True)}</td></tr>")
    per_html = ("<table class='grid'><tr><th>Strategy</th><th>Closed</th><th>Win%</th>"
                "<th>Net P&L</th></tr>" + ("".join(rows) or
                "<tr><td colspan=4 class='muted'>No closed trades yet.</td></tr>") + "</table>")

    # ── Detailed Journal tab ──
    det = []
    for t in reversed(trades):  # newest first
        cls = ""
        if t["status"] == "CLOSED":
            cls = "pos" if (t["pnl"] or 0) >= 0 else "neg"
        pnl_cell = (f"<span class='{cls}'>{_fmt(t['pnl'], money=True)} "
                    f"({t['pnl_pct']:+.0f}%)</span>" if t["status"] == "CLOSED"
                    else "<span class='muted'>OPEN</span>")
        rcell = f"{t['r_multiple']:+.2f}R" if t["r_multiple"] is not None else "—"
        det.append(f"""
        <tr class="hdr">
          <td>#{t['id']}</td><td>{html.escape(t['date'])}</td>
          <td><b>{html.escape(t['strategy'])}</b></td>
          <td>{html.escape(t['underlying'])} {html.escape(t['opt_kind'])}</td>
          <td>{html.escape(t['opt_symbol'])}</td>
          <td>{t['lots']}×{t['lot_size']}</td>
          <td>{pnl_cell}</td><td>{rcell}</td>
        </tr>
        <tr class="detail"><td colspan="8">
          <div class="kv"><span>Entry</span> {now_or(t['entry_ts'])} · spot {_fmt(t['entry_spot'])} · premium ₹{_fmt(t['entry_prem'])}</div>
          <div class="kv"><span>Target</span> {tgt_str(t)}</div>
          <div class="kv"><span>Stop</span> {sl_str(t)}</div>
          <div class="kv"><span>Exit</span> {exit_str(t)}</div>
          <div class="note"><b>Why it entered:</b> {html.escape(t['entry_remarks'] or '')}</div>
          <div class="note"><b>Entry logic:</b> {html.escape(t['entry_logic'] or '')}</div>
          <div class="note"><b>Stop logic:</b> {html.escape(t['sl_logic'] or '')}</div>
          <div class="note"><b>Exit logic:</b> {html.escape(t['exit_logic'] or '')}</div>
        </td></tr>""")
    detail_html = ("<table class='journal'><tr><th>ID</th><th>Date</th><th>Strategy</th>"
                   "<th>Instrument</th><th>Symbol</th><th>Size</th><th>P&L</th><th>R</th></tr>"
                   + ("".join(det) or "<tr><td colspan=8 class='muted'>No trades journalled yet. "
                      "Run live_trade.py during market hours.</td></tr>") + "</table>")

    page = TEMPLATE.format(now=now, cards=card_html, per=per_html, detail=detail_html,
                           strategies=STRATEGY_SHOWCASE, capital=_fmt(st["capital"], money=True))
    out = SITE / "journal.html"
    out.write_text(page, encoding="utf-8")
    write_csv(trades, SITE / "journal.csv")
    write_landing(trades, st, per, now)
    print(f"Wrote {out}")
    print(f"Wrote {SITE / 'index.html'} (landing)")
    print(f"Wrote {SITE / 'journal.csv'}")
    print(f"Net P&L {_fmt(st['net'], money=True)} ({st['ret_pct']:+.2f}%) over "
          f"{st['n_closed']} closed trades, {st['n_open']} open.")


# small formatting helpers used inside the f-string above
def now_or(ts):
    return html.escape(ts.replace("T", " ")) if ts else "—"


def tgt_str(t):
    bits = []
    if t["target_spot"] is not None:
        bits.append(f"spot {_fmt(t['target_spot'])}")
    if t["target_prem"] is not None:
        bits.append(f"premium ₹{_fmt(t['target_prem'])}")
    if t["time_exit_min"]:
        bits.append(f"time-exit {t['time_exit_min']}min")
    return html.escape(" · ".join(bits)) if bits else "—"


def sl_str(t):
    bits = []
    if t["sl_spot"] is not None:
        bits.append(f"spot {_fmt(t['sl_spot'])}")
    if t["sl_prem"] is not None:
        bits.append(f"premium ₹{_fmt(t['sl_prem'])}")
    return html.escape(" · ".join(bits)) if bits else "—"


def exit_str(t):
    if t["status"] != "CLOSED":
        return "—"
    return html.escape(f"{now_or(t['exit_ts'])} · spot {_fmt(t['exit_spot'])} · "
                       f"premium ₹{_fmt(t['exit_prem'])} · {t['exit_reason']}")


STRATEGY_SHOWCASE = """
<p class="muted" style="margin-top:4px">Four researched option-BUYING setups, traded
on NIFTY (NSE) &amp; SENSEX (BSE) nearest-expiry ATM options. Each entry below is
journalled with the exact reason it fired and its planned target &amp; stop.</p>
<table class="grid" style="max-width:100%">
 <tr><th>Strategy</th><th>How it works</th><th>Entry</th><th>Target</th><th>Stop-loss</th></tr>
 <tr><td><b>ORB</b><br><span class="muted">Opening Range Breakout</span></td>
   <td>First real move out of the 15-min opening range.</td>
   <td>Buy ATM CE above range high / ATM PE below range low.</td>
   <td>2× the range width (~1:2 reward).</td>
   <td>Opposite boundary of the range.</td></tr>
 <tr><td><b>5 EMA</b><br><span class="muted">Power of Stocks (S. Pani)</span></td>
   <td>Counter-trend exhaustion off the 5-EMA on 5-min.</td>
   <td>Alert candle fully off the EMA; enter on break of its high (CE) / low (PE).</td>
   <td>1:3 of the risk.</td>
   <td>Beyond the alert candle.</td></tr>
 <tr><td><b>Expiry Gamma</b><br><span class="muted">expiry-day scalp</span></td>
   <td>Cheap, high-gamma ATM momentum on expiry.</td>
   <td>Volume-backed break of opening range with futures above/below VWAP.</td>
   <td>+25 premium points.</td>
   <td>30% of premium; flat within 30 min.</td></tr>
 <tr><td><b>OI + Gap</b><br><span class="muted">option-chain OI</span></td>
   <td>Gap direction + OI walls (max Put-OI = support, max Call-OI = resistance).</td>
   <td>Gap-up: CE on break of OR high above support. Gap-down: mirror.</td>
   <td>The opposing OI wall.</td>
   <td>Opposite side of the opening range.</td></tr>
</table>
<p class="muted" style="font-size:12px;margin-top:14px">Risk per trade ≈ 1% of capital ·
spot-based trades also carry a 40% premium safety-stop · max one trade per strategy
per underlying per day · all positions squared off by 15:25. Simulated fills on each
day's real 5-min candles — no real orders.</p>
"""

def write_landing(trades, st, per, now):
    """The public front door (index.html): headline performance + recent trades."""
    cards = [
        ("Net P&L", _fmt(st["net"], money=True), st["net"] >= 0),
        ("Return on capital", f"{st['ret_pct']:+.2f}%", st["ret_pct"] >= 0),
        ("Win rate", f"{st['win_rate']:.1f}%", st["win_rate"] >= 50),
        ("Profit factor", _fmt(st["pf"]), st["pf"] >= 1),
        ("Closed trades", f"{st['n_closed']}", None),
        ("Max drawdown", _fmt(st["max_dd"], money=True), False),
    ]
    card_html = "".join(
        f'<div class="card"><div class="cap">{html.escape(lbl)}</div>'
        f'<div class="val {"pos" if g else ("neg" if g is False else "")}">{v}</div></div>'
        for lbl, v, g in cards)

    closed = [t for t in trades if t["status"] == "CLOSED"]
    recent = list(reversed(closed))[:10]
    rrows = []
    for t in recent:
        cls = "pos" if (t["pnl"] or 0) >= 0 else "neg"
        rrows.append(
            f"<tr><td>{html.escape(t['date'])}</td><td><b>{html.escape(t['strategy'])}</b></td>"
            f"<td>{html.escape(t['underlying'])} {html.escape(t['opt_kind'])}</td>"
            f"<td class='r {cls}'>{_fmt(t['pnl'], money=True)}</td>"
            f"<td class='r {cls}'>{t['r_multiple']:+.2f}R</td></tr>"
            if t["r_multiple"] is not None else
            f"<tr><td>{html.escape(t['date'])}</td><td><b>{html.escape(t['strategy'])}</b></td>"
            f"<td>{html.escape(t['underlying'])} {html.escape(t['opt_kind'])}</td>"
            f"<td class='r {cls}'>{_fmt(t['pnl'], money=True)}</td><td class='r'>—</td></tr>")
    recent_html = ("<table><tr><th>Date</th><th>Strategy</th><th>Instrument</th>"
                   "<th class='r'>P&L</th><th class='r'>R</th></tr>"
                   + ("".join(rrows) or "<tr><td colspan=5 class='muted'>First session "
                      "pending — trades appear here automatically after market close.</td></tr>")
                   + "</table>")

    (SITE / "index.html").write_text(
        LANDING.format(now=now, capital=_fmt(st["capital"], money=True),
                       cards=card_html, recent=recent_html), encoding="utf-8")


LANDING = """<!doctype html><html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Option Strategy Lab — Automated Paper Trading</title>
<style>
 :root{{--bg:#0e1117;--panel:#161b22;--bd:#30363d;--fg:#e6edf3;--mut:#8b949e;
        --pos:#3fb950;--neg:#f85149;--accent:#58a6ff}}
 *{{box-sizing:border-box}} body{{margin:0;background:var(--bg);color:var(--fg);
   font:15px/1.6 -apple-system,Segoe UI,Roboto,Helvetica,Arial}}
 .wrap{{max-width:980px;margin:0 auto;padding:0 18px 64px}}
 .hero{{padding:54px 0 26px;text-align:center;border-bottom:1px solid var(--bd)}}
 .hero h1{{margin:0;font-size:32px;letter-spacing:-.5px}}
 .hero p{{color:var(--mut);max-width:640px;margin:12px auto 0;font-size:15px}}
 .pill{{display:inline-block;margin-top:16px;padding:5px 12px;border:1px solid var(--bd);
   border-radius:999px;color:var(--mut);font-size:12.5px}}
 .pill b{{color:var(--pos)}}
 .cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin:28px 0}}
 .card{{background:var(--panel);border:1px solid var(--bd);border-radius:12px;padding:16px}}
 .cap{{color:var(--mut);font-size:12px}} .val{{font-size:24px;font-weight:700;margin-top:6px}}
 .pos{{color:var(--pos)}} .neg{{color:var(--neg)}} .muted{{color:var(--mut)}}
 h2{{font-size:16px;margin:30px 0 10px}}
 table{{width:100%;border-collapse:collapse;background:var(--panel);border:1px solid var(--bd);
   border-radius:10px;overflow:hidden;font-size:13.5px}}
 th,td{{text-align:left;padding:9px 12px;border-bottom:1px solid var(--bd)}}
 th{{color:var(--mut);font-weight:600}} .r{{text-align:right}}
 .tiles{{display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));gap:14px;margin:14px 0}}
 a.tile{{display:block;background:var(--panel);border:1px solid var(--bd);border-radius:12px;
   padding:18px;text-decoration:none;color:var(--fg);transition:border-color .15s}}
 a.tile:hover{{border-color:var(--accent)}}
 a.tile .t{{font-weight:700;font-size:15px}} a.tile .d{{color:var(--mut);font-size:12.5px;margin-top:4px}}
 .arrow{{color:var(--accent)}}
 .disc{{margin-top:30px;color:var(--mut);font-size:11.5px;border-top:1px solid var(--bd);padding-top:14px}}
</style></head><body>
<div class="hero"><div class="wrap" style="padding-top:0">
 <h1>📈 Option Strategy Lab</h1>
 <p>Four researched option-BUYING strategies — ORB, 5 EMA (Power of Stocks), Expiry
    Gamma and OI+Gap — traded automatically on NIFTY &amp; SENSEX as paper trades, with
    every entry, exit and stop journalled in full. Updated daily after market close.</p>
 <div class="pill">Virtual capital {capital} · automated · simulated, no real orders</div>
</div></div>
<div class="wrap">
 <div class="cards">{cards}</div>

 <h2>Recent trades</h2>
 {recent}

 <h2>Explore</h2>
 <div class="tiles">
   <a class="tile" href="journal.html"><div class="t">🤖 Strategy Journal <span class="arrow">→</span></div>
     <div class="d">Every trade with the reason it entered, its target &amp; stop logic, and the exit.</div></a>
   <a class="tile" href="journal.html"><div class="t">📚 The Strategies <span class="arrow">→</span></div>
     <div class="d">How each of the four setups defines entry, target and stop-loss.</div></a>
   <a class="tile" href="screener.html"><div class="t">📈 NSE Bullish Screener <span class="arrow">→</span></div>
     <div class="d">Daily scan of NSE stocks for breakout, pre-breakout and pullback setups.</div></a>
   <a class="tile" href="performance.html"><div class="t">📊 Screener Performance <span class="arrow">→</span></div>
     <div class="d">Event-driven backtest of the screener's setups — equity curve and stats.</div></a>
 </div>

 <div class="disc">Educational simulation only. Option buying carries a high risk of loss
  (time decay / IV crush); SEBI studies show a large majority of F&amp;O traders lose money.
  Past simulated results do not predict future performance. Updated {now}.</div>
</div></body></html>"""


TEMPLATE = """<!doctype html><html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Strategy Paper-Trading Journal</title>
<style>
 :root{{--bg:#0e1117;--panel:#161b22;--bd:#30363d;--fg:#e6edf3;--mut:#8b949e;
        --pos:#3fb950;--neg:#f85149;--accent:#58a6ff}}
 *{{box-sizing:border-box}} body{{margin:0;background:var(--bg);color:var(--fg);
   font:14px/1.5 -apple-system,Segoe UI,Roboto,Helvetica,Arial}}
 header{{padding:18px 22px;border-bottom:1px solid var(--bd)}}
 h1{{margin:0;font-size:18px}} .sub{{color:var(--mut);font-size:12px;margin-top:4px}}
 .nav{{margin-top:8px;font-size:13px}} .nav a{{color:var(--accent);text-decoration:none}}
 .nav a:hover{{text-decoration:underline}}
 .tabs{{display:flex;gap:8px;padding:14px 22px 0}}
 .tab{{padding:9px 16px;border:1px solid var(--bd);border-bottom:none;border-radius:8px 8px 0 0;
   background:var(--panel);color:var(--mut);cursor:pointer;font-weight:600}}
 .tab.active{{color:var(--fg);border-color:var(--accent)}}
 .wrap{{padding:18px 22px}} .view{{display:none}} .view.active{{display:block}}
 .cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px}}
 .card{{background:var(--panel);border:1px solid var(--bd);border-radius:10px;padding:14px}}
 .cap{{color:var(--mut);font-size:12px}} .val{{font-size:22px;font-weight:700;margin-top:6px}}
 .pos{{color:var(--pos)}} .neg{{color:var(--neg)}} .muted{{color:var(--mut)}}
 table{{width:100%;border-collapse:collapse;margin-top:16px}}
 th,td{{text-align:left;padding:8px 10px;border-bottom:1px solid var(--bd);font-size:13px}}
 th{{color:var(--mut);font-weight:600}}
 .grid{{max-width:560px}}
 .journal .hdr td{{background:var(--panel);font-size:13px}}
 .journal .detail td{{padding:6px 14px 16px;background:#0b0e13}}
 .kv{{font-size:12.5px;color:var(--fg);margin:2px 0}}
 .kv span{{display:inline-block;width:64px;color:var(--accent);font-weight:600}}
 .note{{font-size:12.5px;color:#c9d1d9;margin:4px 0}}
 .note b{{color:var(--mut);font-weight:600}}
 .disc{{margin-top:22px;color:var(--mut);font-size:11.5px;border-top:1px solid var(--bd);padding-top:12px}}
</style></head><body>
<header><h1>Strategy Paper-Trading Journal</h1>
 <div class="sub">Virtual capital {capital} · option-buying engine (ORB · 5 EMA · Expiry Gamma · OI+Gap) · updated {now} · simulated, no real orders</div>
 <div class="nav"><a href="index.html">🏠 Home</a> · <a href="screener.html">NSE Bullish Screener</a> · <a href="performance.html">📊 Screener performance</a></div>
</header>
<div class="tabs">
 <div class="tab active" data-v="overall">Overall Return</div>
 <div class="tab" data-v="detail">Detailed Journal</div>
 <div class="tab" data-v="strategies">Strategies</div>
</div>
<div class="wrap">
 <div class="view active" id="overall">
   <div class="cards">{cards}</div>
   <h3 style="margin-top:24px">Per-strategy breakdown</h3>
   {per}
 </div>
 <div class="view" id="detail">
   {detail}
 </div>
 <div class="view" id="strategies">
   {strategies}
 </div>
 <div class="disc">Educational simulation only. Option buying carries high risk of loss
  (time decay / IV crush); SEBI studies show a large majority of F&amp;O traders lose money.
  Past simulated results do not predict future performance.</div>
</div>
<script>
 document.querySelectorAll('.tab').forEach(function(tb){{
   tb.onclick=function(){{
     document.querySelectorAll('.tab').forEach(function(x){{x.classList.remove('active')}});
     document.querySelectorAll('.view').forEach(function(x){{x.classList.remove('active')}});
     tb.classList.add('active');
     document.getElementById(tb.dataset.v).classList.add('active');
   }};
 }});
</script>
</body></html>"""


if __name__ == "__main__":
    build()
