#!/usr/bin/env python3
"""v7 信号轮动滚动回测: 对比 none(现状) vs E(不接飞刀)。

窗口 1 年逐月滑动, 策略状态连续(全周期跑一次再切片, MA200 热启动), 各窗口
独立算年化/夏普/回撤, 与同期沪深300对比。输出对比 HTML: 两条年化曲线 +
回撤曲线 + 最近窗口表。

用法: source .venv/bin/activate && python3 -u etf_rot_signal_rolling.py
"""

from datetime import datetime

import pandas as pd
import plotly.graph_objects as go

from backtest import calc_metrics
from etf_rot_signal import BT_START, load_data
from etf_rot_signal_filter import rotation_backtest

WINDOW = 252
STEP = 21


def run(close_df, mode, bench):
    values, trades = rotation_backtest(close_df, filter_mode=mode)
    bench = bench.reindex(values.index).ffill()
    idx = values.index
    rows = []
    for start_pos in range(0, len(idx) - WINDOW + 1, STEP):
        end_pos = start_pos + WINDOW
        s, e = idx[start_pos], idx[end_pos - 1]
        win_sv = values.loc[s:e]
        win_bv = bench.loc[s:e]
        if win_bv.isna().any() or len(win_sv) < WINDOW * 0.9:
            continue
        sm = calc_metrics(win_sv)
        bm = calc_metrics(win_bv)
        win_holds = {t["code"] for t in trades
                     if s <= t["date"] <= e and t["code"]}
        rows.append({
            "start": s.date(), "end": e.date(),
            "annual_return": sm["annual_return"], "sharpe": sm["sharpe"],
            "max_drawdown": sm["max_drawdown"],
            "hs_annual": bm["annual_return"],
            "beat": sm["annual_return"] > bm["annual_return"],
            "holds": " ".join(sorted(win_holds)),
        })
    return values, pd.DataFrame(rows), trades


def summarize(name, df):
    n_beat = df["beat"].sum()
    print(f"[{name}] 窗口数 {len(df)}, 跑赢沪深300 {n_beat} ({n_beat/len(df):.0%})")
    print(f"        窗口年化中位 {df['annual_return'].median():.1f}%  均值 {df['annual_return'].mean():.1f}%")
    print(f"        窗口夏普中位 {df['sharpe'].median():.2f}  回撤中位 {df['max_drawdown'].median():.1f}%")
    return df


def main():
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    print("[1/3] 读取缓存 + 全周期回测...")
    etfs, hs300_s = load_data()
    close_df = pd.DataFrame(etfs).sort_index().ffill()

    val_none, df_none, _ = run(close_df, "none", hs300_s)
    val_e, df_e, _ = run(close_df, "E", hs300_s)

    print("[2/3] 汇总...")
    df_none = summarize("none", df_none)
    df_e = summarize("E", df_e)

    csv_file = f"etf_rot_signal_rolling_{timestamp}.csv"
    df_e.to_csv(csv_file, index=False, encoding="utf-8-sig")
    print(f"      CSV -> {csv_file}")

    print("[3/3] 输出 HTML...")
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=df_none["start"], y=df_none["annual_return"], mode="lines+markers",
                             name="none 年化", line=dict(color="steelblue")))
    fig.add_trace(go.Scatter(x=df_e["start"], y=df_e["annual_return"], mode="lines+markers",
                             name="E 年化", line=dict(color="forestgreen")))
    fig.add_trace(go.Scatter(x=df_none["start"], y=df_none["max_drawdown"], mode="lines+markers",
                             name="none 回撤", yaxis="y2", line=dict(color="darkred", dash="dash")))
    fig.add_trace(go.Scatter(x=df_e["start"], y=df_e["max_drawdown"], mode="lines+markers",
                             name="E 回撤", yaxis="y2", line=dict(color="olive", dash="dash")))
    fig.add_trace(go.Scatter(x=df_e["start"], y=df_e["hs_annual"], mode="lines",
                             name="沪深300年化", line=dict(color="gray", width=1)))
    fig.update_layout(
        title=f"v7 信号轮动滚动回测 none vs E ({timestamp})<br><sup>窗口{WINDOW}日/步{STEP}日 | "
              f"MA200/LB180/GAP1.0/CD20/TRAIL20%/底仓45% | {BT_START} ~ 2026-08</sup>",
        yaxis=dict(title="年化收益 (%)"),
        yaxis2=dict(title="回撤 (%)", overlaying="y", side="right"),
        height=520,
    )
    for name, df in [("none", df_none), ("E", df_e)]:
        df_disp = df.tail(12).copy()
        df_disp["annual"] = df_disp["annual_return"].map(lambda v: f"{v:.1f}%")
        df_disp["dd"] = df_disp["max_drawdown"].map(lambda v: f"{v:.1f}%")
        df_disp["hs"] = df_disp["hs_annual"].map(lambda v: f"{v:.1f}%")
        fig.add_trace(go.Table(
            header=dict(values=[f"{name} 窗口开始", "结束", "年化", "夏普", "回撤", "沪深300", "跑赢", "持仓"]),
            cells=dict(values=[
                df_disp["start"], df_disp["end"], df_disp["annual"], df_disp["sharpe"],
                df_disp["dd"], df_disp["hs"],
                df_disp["beat"].map(lambda b: "✓" if b else "✗"),
                df_disp["holds"],
            ]),
        ))

    html_file = f"etf_rot_signal_rolling_{timestamp}.html"
    fig.write_html(html_file)
    print(f"      HTML -> {html_file}")
    print("\n完成.")


if __name__ == "__main__":
    main()
