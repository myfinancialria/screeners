"""
Paper trading on live Fyers prices — equity + F&O (nearest expiry only).

No real orders are ever placed. Trades are simulated and stored locally in
paper.db, then marked to live market prices to compute P&L.

EQUITY (qty in shares, full Fyers symbol):
    python3 paper.py buy  NSE:SBIN-EQ 10
    python3 paper.py sell NSE:SBIN-EQ 10
    python3 paper.py buy  NSE:RELIANCE-EQ 5 --price 1450     # override fill price

F&O (qty in LOTS, give the UNDERLYING; nearest expiry is auto-picked):
    python3 paper.py buy NIFTY 1 --fut                       # nearest NIFTY future
    python3 paper.py buy NIFTY 2 --ce 23400                  # 2 lots 23400 CE
    python3 paper.py buy BANKNIFTY 1 --pe ATM                # ATM put
    python3 paper.py sell NIFTY 1 --fut

VIEW:
    python3 paper.py positions       # open positions + live MTM
    python3 paper.py pnl             # realized + unrealized + net worth
    python3 paper.py trades          # full ledger
    python3 paper.py price NIFTY --fut
    python3 paper.py setcapital 500000
    python3 paper.py reset           # wipe all paper trades
"""
import argparse
from datetime import datetime

import paper_db as db
from fyers_data import ltp, resolve_fo


# ----------------------------- position engine -----------------------------

def _sign(x):
    return (x > 0) - (x < 0)


def build_positions(trades):
    """Replay trades into per-symbol positions (avg cost) and realized P&L."""
    pos = {}  # symbol -> dict(qty, avg, realized, segment, lot_size, expiry)
    for t in trades:
        s = t["symbol"]
        p = pos.setdefault(s, {
            "qty": 0, "avg": 0.0, "realized": 0.0,
            "segment": t["segment"], "lot_size": t["lot_size"], "expiry": t["expiry"],
        })
        d = t["qty"] if t["side"] == "BUY" else -t["qty"]
        price = t["price"]
        q, avg = p["qty"], p["avg"]

        if q == 0 or _sign(d) == _sign(q):
            # opening or extending
            total = abs(q) + abs(d)
            p["avg"] = (avg * abs(q) + price * abs(d)) / total
            p["qty"] = q + d
        else:
            # reducing / closing / flipping
            closing = min(abs(d), abs(q))
            if q > 0:
                p["realized"] += (price - avg) * closing
            else:
                p["realized"] += (avg - price) * closing
            new_q = q + d
            if abs(d) > abs(q):        # flipped through zero -> remainder opens at price
                p["avg"] = price
            elif new_q == 0:
                p["avg"] = 0.0
            p["qty"] = new_q
    return pos


def open_positions(pos):
    return {s: p for s, p in pos.items() if p["qty"] != 0}


# ------------------------------- formatting --------------------------------

def rupee(x):
    return f"₹{x:,.2f}"


def cmd_positions(_):
    pos = build_positions(db.all_trades())
    openp = open_positions(pos)
    if not openp:
        print("No open positions.")
        return
    live = ltp(list(openp.keys()))
    print(f"{'SYMBOL':<28}{'SEG':<4}{'QTY':>7}{'AVG':>11}{'LTP':>11}{'MTM P&L':>14}")
    print("-" * 75)
    total_unreal = 0.0
    for s, p in openp.items():
        cur = live.get(s) or 0.0
        unreal = (cur - p["avg"]) * p["qty"]
        total_unreal += unreal
        print(f"{s:<28}{p['segment']:<4}{p['qty']:>7}{p['avg']:>11.2f}"
              f"{cur:>11.2f}{unreal:>14,.2f}")
    print("-" * 75)
    print(f"{'Unrealized MTM':<61}{total_unreal:>14,.2f}")


def cmd_pnl(_):
    pos = build_positions(db.all_trades())
    realized = sum(p["realized"] for p in pos.values())
    openp = open_positions(pos)
    unreal = 0.0
    if openp:
        live = ltp(list(openp.keys()))
        unreal = sum(((live.get(s) or 0.0) - p["avg"]) * p["qty"]
                     for s, p in openp.items())
    capital = db.get_capital()
    print("Paper trading P&L")
    print("-" * 40)
    print(f"  Virtual capital : {rupee(capital)}")
    print(f"  Realized P&L    : {rupee(realized)}")
    print(f"  Unrealized P&L  : {rupee(unreal)}")
    print(f"  Total P&L       : {rupee(realized + unreal)}")
    print(f"  Net worth       : {rupee(capital + realized + unreal)}")
    if capital:
        print(f"  Return          : {(realized + unreal) / capital * 100:+.2f}%")


def cmd_trades(_):
    rows = db.all_trades()
    if not rows:
        print("No trades yet.")
        return
    print(f"{'ID':>4} {'TIME':<17}{'SYMBOL':<26}{'SIDE':<5}{'QTY':>7}{'PRICE':>11}")
    print("-" * 74)
    for t in rows:
        ts = t["ts"][:16].replace("T", " ")
        print(f"{t['id']:>4} {ts:<17}{t['symbol']:<26}{t['side']:<5}"
              f"{t['qty']:>7}{t['price']:>11.2f}")


# ------------------------------- order entry -------------------------------

def _resolve_instrument(args):
    """Return (symbol, segment, lot_size, expiry) from CLI args."""
    if args.fut or args.ce is not None or args.pe is not None:
        if args.fut:
            kind, strike = "FUT", None
        elif args.ce is not None:
            kind, strike = "CE", args.ce
        else:
            kind, strike = "PE", args.pe
        symbol, lot_size, expiry = resolve_fo(
            args.instrument, kind, strike, exchange=args.exchange)
        return symbol, "FO", lot_size, expiry
    # equity: instrument is the full Fyers symbol, qty in shares
    return args.instrument, "EQ", 1, None


def _place(side, args):
    symbol, segment, lot_size, expiry = _resolve_instrument(args)
    units = args.qty * lot_size  # F&O qty is in lots
    price = args.price if args.price is not None else ltp(symbol).get(symbol)
    if not price:
        raise SystemExit(f"Could not get a price for {symbol}.")
    db.add_trade(
        ts=datetime.now().isoformat(timespec="seconds"),
        symbol=symbol, segment=segment, side=side,
        qty=units, price=price, lot_size=lot_size, expiry=expiry, note=args.note,
    )
    lots = f" ({args.qty} lot x {lot_size})" if segment == "FO" else ""
    exp = f"  exp {expiry}" if expiry else ""
    print(f"✓ {side} {units}{lots} {symbol} @ {price:.2f}{exp}  [PAPER]")


# --------------------------------- misc ------------------------------------

def cmd_price(args):
    symbol, _, lot_size, expiry = _resolve_instrument(args)
    px = ltp(symbol).get(symbol)
    exp = f"  (nearest expiry {expiry}, lot {lot_size})" if expiry else ""
    print(f"{symbol}: {px}{exp}")


def cmd_setcapital(args):
    db.set_capital(args.amount)
    print(f"Virtual capital set to {rupee(args.amount)}")


def cmd_reset(args):
    if not args.yes:
        print("This wipes ALL paper trades. Re-run with --yes to confirm.")
        return
    db.reset()
    print("All paper trades deleted.")


# --------------------------------- CLI -------------------------------------

def add_instrument_args(sp):
    sp.add_argument("instrument", help="EQ: full symbol e.g. NSE:SBIN-EQ | F&O: underlying e.g. NIFTY")
    sp.add_argument("qty", type=int, help="shares (EQ) or lots (F&O)")
    sp.add_argument("--fut", action="store_true", help="nearest-expiry future")
    sp.add_argument("--ce", help="call option strike (number or ATM)")
    sp.add_argument("--pe", help="put option strike (number or ATM)")
    sp.add_argument("--exchange", default="NSE", help="NSE (default) or BSE")
    sp.add_argument("--price", type=float, help="override fill price (default: live LTP)")
    sp.add_argument("--note", help="optional note")


def main():
    db.init()
    ap = argparse.ArgumentParser(description="Paper trade on live Fyers prices.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    for name in ("buy", "sell"):
        sp = sub.add_parser(name, help=f"{name} (paper)")
        add_instrument_args(sp)
        sp.set_defaults(func=lambda a, s=name.upper(): _place(s, a))

    sub.add_parser("positions", help="open positions + live MTM").set_defaults(func=cmd_positions)
    sub.add_parser("pnl", help="P&L summary").set_defaults(func=cmd_pnl)
    sub.add_parser("trades", help="trade ledger").set_defaults(func=cmd_trades)

    pp = sub.add_parser("price", help="live price of an instrument")
    add_instrument_args(pp)  # reuse (qty ignored)
    pp.set_defaults(func=cmd_price)

    cp = sub.add_parser("setcapital", help="set virtual capital")
    cp.add_argument("amount", type=float)
    cp.set_defaults(func=cmd_setcapital)

    rp = sub.add_parser("reset", help="wipe all paper trades")
    rp.add_argument("--yes", action="store_true")
    rp.set_defaults(func=cmd_reset)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
