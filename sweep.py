#!/usr/bin/env python3
"""PE parameter sweep: test PE_BUY x PE_SELL combos, output heatmap."""

import sys
import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from datetime import datetime

import baostock as bs
from backtest import (
    START, END,
    prepare_backtest_data, run_backtest, calc_metrics,
)


def main():
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    lg = bs.login()
    if lg.error_code != "0":
        print(f"baostock login failed: {lg.error_msg}", file=sys.stderr)
        sys.exit(1)

    print("=== PE 参数扫描 ===")

    close_df, pe_df, signals, month_ends, etf_close, etf_dates_pd, hs300_data = \
        prepare_backtest_data()

    hs300_s = pd.Series(hs300_data["close"], index=pd.to_datetime(hs300_data["dates"])).sort_index()
    hs300_s = hs300_s.reindex(close_df.index).ffill()
    hs300_values = hs300_s[START:END]

    buy_levels = [10, 20, 30, 40]
    sell_levels = [50, 60, 70, 80]

    results = []
    total = len(buy_levels) * len(sell_levels)
    n = 0

    for buy_pct in buy_levels:
        for sell_pct in sell_levels:
            n += 1
            print(f"\n[{n}/{total}] 买<{buy_pct}%  卖>{sell_pct}%")

            strategy_values, trade_log = run_backtest(
                close_df, pe_df, signals, month_ends,
                etf_close, etf_dates_pd,
                buy_pct=buy_pct, sell_pct=sell_pct,
            )

            common = strategy_values.dropna().index.intersection(hs300_values.dropna().index)
            sv = strategy_values.reindex(common).ffill()
            bv = hs300_values.reindex(common).ffill()

            sm = calc_metrics(sv)
            bm = calc_metrics(bv)

            results.append({
                "buy_pct": buy_pct,
                "sell_pct": sell_pct,
                "annual_return": sm["annual_return"],
                "sharpe": sm["sharpe"],
                "max_drawdown": sm["max_drawdown"],
                "benchmark_return": bm["annual_return"],
            })
            print(f"       年化:{sm['annual_return']:5.1f}%  夏普:{sm['sharpe']:.2f}  回撤:{sm['max_drawdown']:.1f}%")

    df = pd.DataFrame(results)

    fig = make_subplots(
        rows=1, cols=3,
        subplot_titles=("年化收益 (%)", "夏普比率", "最大回撤 (%)"),
        horizontal_spacing=0.12,
    )

    configs = [
        ("annual_return", "RdBu", True),        # positive=good, zmid=0
        ("sharpe", "RdBu", True),
        ("max_drawdown", "RdBu_r", False),       # lower=better, reversed colorscale
    ]

    for i, (col, colorscale, symmetric) in enumerate(configs):
        pivot = df.pivot(index="buy_pct", columns="sell_pct", values=col)
        fmt = ".1f" if col != "sharpe" else ".2f"

        trace = go.Heatmap(
            z=pivot.values,
            x=[str(v) for v in pivot.columns],
            y=[str(v) for v in pivot.index],
            colorscale=colorscale,
            zmid=0 if symmetric else None,
            text=[[f"{v:{fmt}}" for v in row] for row in pivot.values],
            texttemplate="%{text}",
            textfont={"size": 12},
            hovertemplate="BUY:%{y}%  SELL:%{x}%<br>值:%{z:" + fmt + "}<extra></extra>",
            showscale=False,
        )
        fig.add_trace(trace, row=1, col=i + 1)

    fig.update_xaxes(title_text="PE_SELL (%)", row=1, col=1)
    fig.update_xaxes(title_text="PE_SELL (%)", row=1, col=2)
    fig.update_xaxes(title_text="PE_SELL (%)", row=1, col=3)
    fig.update_yaxes(title_text="PE_BUY (%)", row=1, col=1)

    fig.update_layout(
        title=f"PE 参数扫描 (PE_BUY × PE_SELL) | {START} ~ {END} | 月频 最多{5}只",
        height=500,
    )

    html_file = f"sweep_{timestamp}.html"
    fig.write_html(html_file)
    print(f"\nHTML -> {html_file}")

    csv_file = f"sweep_{timestamp}.csv"
    df.to_csv(csv_file, index=False, encoding="utf-8-sig")
    print(f"CSV -> {csv_file}")

    bs.logout()
    print("\n完成.")


if __name__ == "__main__":
    main()
