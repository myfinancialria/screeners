# FYERS Pattern / S-R / Trendline Scanner

Identifies tradable bullish/bearish setups on Indian stocks using live FYERS
data. It detects candlestick patterns, draws support/resistance and trendlines,
and **only signals when context confirms the pattern** (trend + volume + S/R +
momentum) — because patterns alone are noisy.

> This finds setups to review. It does **not** place trades.

## Setup
Already connected via the parent project. Make sure the FYERS token is fresh:
```
python3 auth.py        # refresh token if a command returns an auth error
```

## Usage

**Analyse one stock (detailed + chart):**
```
python3 analyze.py NSE:SBIN-EQ --chart
python3 analyze.py NSE:RELIANCE-EQ --resolution 15 --days 30 --chart   # intraday
```

**Scan the Nifty 50 and rank setups:**
```
python3 analyze.py --universe nifty50 --chart
python3 analyze.py --universe nifty50 --side bullish --min-score 60 --top 15
python3 analyze.py --symbols NSE:SBIN-EQ,NSE:INFY-EQ --chart
```

Options: `--resolution` (D, 5, 15, 60…), `--days`, `--side` both|bullish|bearish,
`--min-score`, `--top`, `--chart`, `--out <csv>`.

## Outputs
- Ranked table in the terminal
- CSV under `outputs/` (score, plan, confirmations per stock)
- Annotated PNG charts under `charts/` (with `--chart`)

## How a signal is built
| Layer | Module | What it does |
|---|---|---|
| Data | `history.py` | FYERS OHLCV → pandas (chunked for intraday) |
| Indicators | `indicators.py` | EMA 20/50/200, RSI, ATR, ADX, volume ratio |
| Candlesticks | `candles.py` | engulfing, hammer, star, marubozu, doji, inside bar, piercing/dark-cloud |
| Levels | `levels.py` | swing-pivot S/R zones, sloped trendlines, market structure |
| Confirmation | `signals.py` | scores bull vs bear; needs pattern **+** context; builds entry/SL/targets |
| Chart | `chart.py` | candles + EMAs + S/R + trendlines + trade plan |
| CLI | `analyze.py` | single/universe scan, rank, CSV, charts |

## Scoring (summary)
Bullish points: breakout > 20-bar high (+25), volume ≥1.5× (+20), bullish candle (+20),
at support (+15), EMA20>EMA50 (+15), above 200 EMA (+10), ADX≥20 (+10), RSI 55-75 (+10),
higher highs/lows (+10), closes top of range (+5). Penalties for below-200-EMA, weak
volume, over-extended bar. Bearish is the mirror. Direction needs score ≥ 50.

## Full-market scan (all of NSE)
`scan_all.py` scans the **entire NSE** (~2,450 equities) concurrently,
rate-limited, and surfaces bullish setups **early**:

| Setup | Meaning |
|---|---|
| `BREAKOUT` | closing above the 20-bar high *now* on volume |
| `PRE_BREAKOUT` | coiling within 3% under the high, volatility squeezing — likely soon |
| `PULLBACK` | uptrend dip back to EMA20/support — continuation entry |

```
python3 scan_all.py                              # full NSE, all bullish setups
python3 scan_all.py --setup pre_breakout         # only "about to break out"
python3 scan_all.py --setup breakout --chart --top 30
python3 scan_all.py --min-value 20 --min-price 100   # tighter liquidity
python3 scan_all.py --universe nifty50           # quick test
```
Options: `--setup all|breakout|pre_breakout|pullback|bullish`, `--min-value`
(avg traded value ₹cr), `--min-price`, `--min-score`, `--top`, `--workers`,
`--rps` (API rate cap), `--chart`. Full run ≈ 5-6 min; output ranked table +
CSV in `outputs/`. Columns include `%toHigh` (distance to breakout) and `₹Cr`
(liquidity).

Data caveats: the `-EQ` master also contains some ETFs/liquid funds (e.g.
MON100, SBILIQETF) — raise `--min-value` or ignore obvious fund tickers. Very
high `VOL` (e.g. 15×+) on thin names can be noise.

## Notes / next steps
- Token expires daily — re-run `auth.py`.
- F&O / index symbols also work (e.g. `NSE:NIFTY50-INDEX`, futures via the symbol).
- Ideas: Telegram/email alerts, a backtest of the score, a Streamlit dashboard,
  EOD cron scan. Ask and they can be added.
```
