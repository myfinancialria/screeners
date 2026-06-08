"""
Strategy paper-trading journal — a local SQLite store (nothing leaves your machine).

DB lives at  fyers-connect/strategy.db  (separate from the manual paper.db).

Every automated trade is one row carrying the FULL story: which strategy fired,
why it entered, the EXACT entry price & time, the planned target & stop, the *logic*
behind each, and on exit the realised P&L plus the exact price & time the stop or
target was hit. Handles both option BUYING (CE/PE) and stock FUTURES (long/short).
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).with_name("strategy.db")

# Virtual starting capital (₹).  Change with: journal_db.set_capital(amount)
DEFAULT_CAPITAL = 1_000_000.0

# Columns added after the first release — migrated onto existing DBs automatically.
_EXTRA_COLS = {
    "instrument_type": "TEXT DEFAULT 'OPT'",   # OPT | FUT
    "side": "TEXT DEFAULT 'BUY'",              # BUY (options) | LONG | SHORT (futures)
    "sl_hit_ts": "TEXT",
    "sl_hit_price": "REAL",
    "tgt_hit_ts": "TEXT",
    "tgt_hit_price": "REAL",
}


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _migrate(c) -> None:
    have = {r["name"] for r in c.execute("PRAGMA table_info(journal)")}
    for col, decl in _EXTRA_COLS.items():
        if col not in have:
            c.execute(f"ALTER TABLE journal ADD COLUMN {col} {decl}")


def init() -> None:
    with connect() as c:
        c.execute("""
            CREATE TABLE IF NOT EXISTS journal (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                date          TEXT    NOT NULL,
                strategy      TEXT    NOT NULL,
                underlying    TEXT    NOT NULL,
                exchange      TEXT    NOT NULL,
                instrument_type TEXT  DEFAULT 'OPT',   -- OPT | FUT
                opt_symbol    TEXT    NOT NULL,         -- option or future Fyers symbol
                opt_kind      TEXT    NOT NULL,         -- CE | PE | FUT
                side          TEXT    DEFAULT 'BUY',    -- BUY | LONG | SHORT
                strike        REAL,
                lots          INTEGER NOT NULL,
                lot_size      INTEGER NOT NULL,
                qty           INTEGER NOT NULL,
                status        TEXT    NOT NULL,         -- OPEN | CLOSED

                entry_ts      TEXT    NOT NULL,         -- exact fill time
                entry_spot    REAL,
                entry_prem    REAL    NOT NULL,         -- exact fill price (premium or future px)

                sl_spot       REAL,
                target_spot   REAL,
                sl_prem       REAL,
                target_prem   REAL,
                time_exit_min INTEGER,
                risk_amt      REAL,

                exit_ts       TEXT,
                exit_spot     REAL,
                exit_prem     REAL,
                exit_reason   TEXT,
                sl_hit_ts     TEXT,                     -- exact time stop was hit
                sl_hit_price  REAL,                     -- exact price stop filled
                tgt_hit_ts    TEXT,                     -- exact time target was hit
                tgt_hit_price REAL,                     -- exact price target filled
                pnl           REAL,
                pnl_pct       REAL,
                r_multiple    REAL,

                entry_remarks TEXT,
                entry_logic   TEXT,
                sl_logic      TEXT,
                exit_logic    TEXT
            )
        """)
        _migrate(c)
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


_OPEN_COLS = [
    "date", "strategy", "underlying", "exchange", "instrument_type", "opt_symbol",
    "opt_kind", "side", "strike", "lots", "lot_size", "qty", "status", "entry_ts",
    "entry_spot", "entry_prem", "sl_spot", "target_spot", "sl_prem", "target_prem",
    "time_exit_min", "risk_amt", "entry_remarks", "entry_logic", "sl_logic", "exit_logic",
]


def open_trade(**kw) -> int:
    kw.setdefault("status", "OPEN")
    kw.setdefault("instrument_type", "OPT")
    kw.setdefault("side", "BUY")
    vals = [kw.get(col) for col in _OPEN_COLS]
    with connect() as c:
        c.execute(
            f"INSERT INTO journal({','.join(_OPEN_COLS)}) "
            f"VALUES({','.join('?' for _ in _OPEN_COLS)})", vals,
        )
        return c.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]


def close_trade(trade_id, exit_ts, exit_spot, exit_prem, exit_reason):
    """Close a trade, compute side-aware P&L, and record the exact SL/target hit."""
    with connect() as c:
        row = c.execute("SELECT * FROM journal WHERE id=?", (trade_id,)).fetchone()
        if row is None or row["status"] == "CLOSED":
            return
        qty = row["qty"]
        entry = row["entry_prem"]
        sign = -1.0 if row["side"] == "SHORT" else 1.0      # short futures profit when px falls
        pnl = sign * (exit_prem - entry) * qty
        pnl_pct = sign * (exit_prem / entry - 1.0) * 100.0 if entry else 0.0
        risk = row["risk_amt"] or 0.0
        r_mult = (pnl / risk) if risk else None

        sl_hit_ts = sl_hit_price = tgt_hit_ts = tgt_hit_price = None
        if exit_reason.startswith("Stop"):
            sl_hit_ts, sl_hit_price = exit_ts, exit_prem
        elif exit_reason.startswith("Target"):
            tgt_hit_ts, tgt_hit_price = exit_ts, exit_prem

        c.execute(
            "UPDATE journal SET status='CLOSED', exit_ts=?, exit_spot=?, exit_prem=?, "
            "exit_reason=?, sl_hit_ts=?, sl_hit_price=?, tgt_hit_ts=?, tgt_hit_price=?, "
            "pnl=?, pnl_pct=?, r_multiple=? WHERE id=?",
            (exit_ts, exit_spot, exit_prem, exit_reason, sl_hit_ts, sl_hit_price,
             tgt_hit_ts, tgt_hit_price, pnl, pnl_pct, r_mult, trade_id),
        )


def open_trades():
    with connect() as c:
        return [dict(r) for r in c.execute(
            "SELECT * FROM journal WHERE status='OPEN' ORDER BY id")]


def all_trades():
    with connect() as c:
        return [dict(r) for r in c.execute("SELECT * FROM journal ORDER BY id")]


def has_trade_today(date, strategy, underlying) -> bool:
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
