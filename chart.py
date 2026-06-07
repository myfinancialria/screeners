"""
Draw an annotated candlestick chart for one analysis result:
candles + EMA20/50 + horizontal S/R zones + sloped trendlines + the trade plan
(entry / stop / target). Saves a PNG.
"""
import matplotlib
matplotlib.use("Agg")
import mplfinance as mpf

from levels import line_value


def draw(analysis: dict, out_path: str, view: int = 150) -> str:
    df = analysis["_df"]
    levels = analysis["_levels"]
    tls = analysis["_trendlines"]
    plan = analysis.get("plan") or {}
    n = len(df)
    start = max(0, n - view)

    plot_df = df.iloc[start:][["open", "high", "low", "close", "volume"]].rename(
        columns=str.capitalize
    )

    adds = [
        mpf.make_addplot(df["ema20"].iloc[start:], color="#1f77b4", width=0.9),
        mpf.make_addplot(df["ema50"].iloc[start:], color="#ff7f0e", width=0.9),
    ]
    if df["ema200"].iloc[start:].notna().any():
        adds.append(mpf.make_addplot(df["ema200"].iloc[start:], color="#7f7f7f", width=0.8))

    # horizontal S/R + trade-plan lines
    hl_prices, hl_colors, hl_styles = [], [], []
    for L in levels:
        hl_prices.append(L["price"])
        hl_colors.append("green" if L["kind"] == "support" else "red")
        hl_styles.append("--")
    for key, color in (("entry", "dodgerblue"), ("stop_loss", "crimson"),
                       ("target1", "limegreen"), ("target2", "darkgreen")):
        if plan.get(key):
            hl_prices.append(plan[key])
            hl_colors.append(color)
            hl_styles.append("-")

    # sloped trendlines
    alines, acolors = [], []
    for key, color in (("support", "green"), ("resistance", "red")):
        if key in tls:
            line = tls[key]
            xa, xb = max(start, line[2]), n - 1
            alines.append([(df.index[xa], line_value(line, xa)),
                           (df.index[xb], line_value(line, xb))])
            acolors.append(color)

    d = analysis["direction"]
    title = (f"{analysis['symbol']}  {d}  score={analysis['score']}  "
             f"close={analysis['close']}  RSI={analysis['rsi']}  {analysis['structure']}")

    kwargs = dict(
        type="candle", style="charles", volume=True, addplot=adds,
        figratio=(16, 9), figscale=1.3, tight_layout=True,
        title=title,
        savefig=dict(fname=out_path, dpi=120, bbox_inches="tight"),
    )
    if hl_prices:
        kwargs["hlines"] = dict(hlines=hl_prices, colors=hl_colors,
                                linestyle=hl_styles, linewidths=0.8)
    if alines:
        kwargs["alines"] = dict(alines=alines, colors=acolors, linewidths=1.3)

    mpf.plot(plot_df, **kwargs)
    return out_path
