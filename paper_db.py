"""
Safe local storage for paper trades — a single SQLite file on your machine.

Nothing leaves your computer. The DB lives at  fyers-connect/paper.db
"""
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).with_name("paper.db")

# Virtual starting capital (₹). Change with: paper.py setcapital <amount>
DEFAULT_CAPITAL = 1_000_000.0


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init() -> None:
    with connect() as c:
        c.execute("""
            CREATE TABLE IF NOT EXISTS trades (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                ts        TEXT    NOT NULL,         -- ISO timestamp
                symbol    TEXT    NOT NULL,         -- Fyers symbol
                segment   TEXT    NOT NULL,         -- EQ | FO
                side      TEXT    NOT NULL,         -- BUY | SELL
                qty       INTEGER NOT NULL,         -- units (lots*lot_size for FO)
                price     REAL    NOT NULL,         -- per-unit fill price
                lot_size  INTEGER DEFAULT 1,
                expiry    TEXT,                     -- for FO
                note      TEXT
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS meta (
                key TEXT PRIMARY KEY,
                val TEXT
            )
        """)
        cur = c.execute("SELECT val FROM meta WHERE key='capital'")
        if cur.fetchone() is None:
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


def add_trade(ts, symbol, segment, side, qty, price, lot_size=1, expiry=None, note=None):
    with connect() as c:
        c.execute(
            "INSERT INTO trades(ts,symbol,segment,side,qty,price,lot_size,expiry,note)"
            " VALUES(?,?,?,?,?,?,?,?,?)",
            (ts, symbol, segment, side, qty, price, lot_size, expiry, note),
        )
        return c.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]


def all_trades():
    with connect() as c:
        return [dict(r) for r in c.execute("SELECT * FROM trades ORDER BY id")]


def reset():
    with connect() as c:
        c.execute("DELETE FROM trades")
