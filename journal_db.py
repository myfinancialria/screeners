"""
Strategy paper-trading journal — a local SQLite store (nothing leaves your machine).

DB lives at  fyers-connect/strategy.db  (separate from the manual paper.db).

Every automated trade is one row carrying the FULL story: which strategy fired,
why it entered, the planned target & stop, the *logic* behind each, and on exit
the realised P&L, R-multiple and the exact reason it was closed. That is what the
journal / performance reports read back.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).with_name("strategy.db")

# Virtual starting capital (₹).  Change with: journal_db.set_capital(amount)
DEFAULT_CAPITAL = 1_000_000.0


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init() -> None:
    with connect() as c:
        c.execute("""
            CREATE TABLE IF NOT EXISTS journal (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                date          TEXT    NOT NULL,   -- YYYY-MM-DD (IST)
                strategy      TEXT    NOT NULL,
                underlying    TEXT    NOT NULL,   -- NIFTY | SENSEX
                exchange      TEXT    NOT NULL,   -- NSE | BSE
                opt_symbol    TEXT    NOT NULL,   -- resolved Fyers option symbol
                opt_kind      TEXT    NOT NULL,   -- CE | PE
                strike        REAL,
                lots          INTEGER NOT NULL,
                lot_size      INTEGER NOT NULL,
                qty           INTEGER NOT NULL,   -- lots * lot_size
                status        TEXT    NOT NULL,   -- OPEN | CLOSED

                entry_ts      TEXT    NOT NULL,
                entry_spot    REAL,
                entry_prem    REAL    NOT NULL,

                sl_spot       REAL,               -- spot level that trips the stop
                target_spot   REAL,               -- spot level that books target
                sl_prem       REAL,               -- premium level that trips the stop
                target_prem   REAL,               -- premium level that books target
                time_exit_min INTEGER,            -- hard time-based exit (minutes)
                risk_amt      REAL,               -- ₹ risked at entry (for R-multiple)

                exit_ts       TEXT,
                exit_spot     REAL,
                exit_prem     REAL,
                exit_reason   TEXT,
                pnl           REAL,
                pnl_pct       REAL,
                r_multiple    REAL,

                entry_remarks TEXT,   -- WHY it entered (plain-English remark)
                entry_logic   TEXT,   -- the entry rule that fired
                sl_logic      TEXT,   -- the stop-loss rule
                exit_logic    TEXT    -- the planned exit / target rule
            )
        """)
        c.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, val TEXT)")
        if c.execute("SELECT val FROM meta WHERE key='capital'").fetchone() is None:
            c.execute("INSERT INTO meta(key,val) VALUES('capital',?)",
                      (str(DEFAULT_CAPITAL),))


def get_capital() -> float:
    with connect() as c:
        row = c.execute("SELECT val FROM meta WHERE key='capital'").fetchone()
        return float(row["val"]) if row else DEFAULT_CAPITAL


def set_capital(amount: float) -> None:
    with connect() as c:
        c.execute("INSERT INTO meta(key,val) VALUES('capital',?) "
                  "ON CONFLICT(key) DO UPDATE SET val=excluded.val", (str(amount),))


def open_trade(**kw) -> int:
    """Insert a new OPEN trade. Pass keyword columns; missing ones default to NULL."""
    cols = [
        "date", "strategy", "underlying", "exchange", "opt_symbol", "opt_kind",
        "strike", "lots", "lot_size", "qty", "status", "entry_ts", "entry_spot",
        "entry_prem", "sl_spot", "target_spot", "sl_prem", "target_prem",
        "time_exit_min", "risk_amt", "entry_remarks", "entry_logic", "sl_logic",
        "exit_logic",
    ]
    kw.setdefault("status", "OPEN")
    vals = [kw.get(col) for col in cols]
    with connect() as c:
        c.execute(
            f"INSERT INTO journal({','.join(cols)}) "
            f"VALUES({','.join('?' for _ in cols)})", vals,
        )
        return c.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]


def close_trade(trade_id, exit_ts, exit_spot, exit_prem, exit_reason):
    """Close an OPEN trade and compute P&L / R-multiple (option BUYING only)."""
    with connect() as c:
        row = c.execute("SELECT * FROM journal WHERE id=?", (trade_id,)).fetchone()
        if row is None or row["status"] == "CLOSED":
            return
        qty = row["qty"]
        entry = row["entry_prem"]
        pnl = (exit_prem - entry) * qty
        pnl_pct = (exit_prem / entry - 1.0) * 100.0 if entry else 0.0
        risk = row["risk_amt"] or 0.0
        r_mult = (pnl / risk) if risk else None
        c.execute(
            "UPDATE journal SET status='CLOSED', exit_ts=?, exit_spot=?, exit_prem=?, "
            "exit_reason=?, pnl=?, pnl_pct=?, r_multiple=? WHERE id=?",
            (exit_ts, exit_spot, exit_prem, exit_reason, pnl, pnl_pct, r_mult, trade_id),
        )


def open_trades():
    with connect() as c:
        return [dict(r) for r in c.execute(
            "SELECT * FROM journal WHERE status='OPEN' ORDER BY id")]


def all_trades():
    with connect() as c:
        return [dict(r) for r in c.execute("SELECT * FROM journal ORDER BY id")]


def has_trade_today(date, strategy, underlying) -> bool:
    """True if this strategy already fired on this underlying today (1 trade/day cap)."""
    with connect() as c:
        row = c.execute(
            "SELECT 1 FROM journal WHERE date=? AND strategy=? AND underlying=? LIMIT 1",
            (date, strategy, underlying)).fetchone()
        return row is not None


def reset() -> None:
    with connect() as c:
        c.execute("DELETE FROM journal")


if __name__ == "__main__":
    init()
    print(f"Journal DB ready at {DB_PATH}  |  capital ₹{get_capital():,.0f}  |  "
          f"{len(all_trades())} trades on record")
