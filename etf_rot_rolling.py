#!/usr/bin/env python3
"""v5 行业ETF轮动滚动回测: 1年窗口逐月滑动, 看策略在每段时期的稳定性。

策略状态连续 (全周期跑一次再按窗口切片), 保证 MA200 热启动; 各窗口独立计算
年化/夏普/最大回撤, 与同期沪深300对比。统计跑赢基准的窗口比例, 判断是否存在
过拟合单一时期的风险。

用法: source .venv/bin/activate && python3 -u etf_rot_rolling.py
"""

from datetime import datetime

import numpy as np
import pandas as pd
import plotly.graph_objects as go

from backtest import calc_metrics
from etf_rotation import BT_START, load_data, rotation_backtest

WINDOW = 252    # 窗口长度 (交易日, 约1年)
STEP = 21       # 滑动步长 (交易日, 约1月)


def main():
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    print("[1/3] 读取缓存 + 全周期回测...")
    etfs, hs300_s = load_data()
    close_df = pd.DataFrame(etfs).sort_index().ffill()
    values, trades = rotation_backtest(close_df)
    hs300_s = hs300_s.reindex(values.index).ffill()

    print("[2/3] 滚动窗口计算...")
    idx = values.index
    rows = []
    for start_pos in range(0, len(idx) - WINDOW + 1, STEP):
        end_pos = start_pos + WINDOW
        s, e = idx[start_pos], idx[end_pos - 1]
        win_sv = values.loc[s:e]
        win_bv = hs300_s.loc[s:e]
        if win_bv.isna().any() or len(win_sv) < WINDOW * 0.9:
            continue
        sm = calc_metrics(win_sv)
        bm = calc_metrics(win_bv)
        win_holds = {t["code"] for t in trades
                     if s <= t["date"] <= e and t["code"]}
        rows.append({
            "start": s.date(), "end": e.date(),
            "annual_return": sm["annual_return"], "sharpe": sm["sharpe"],
            "max_drawdown": sm["max_drawdown"], "total_return": sm["total_return"],
            "hs_annual": bm["annual_return"], "hs_dd": bm["max_drawdown"],
            "beat": sm["annual_return"] > bm["annual_return"],
            "holds": " ".join(sorted(win_holds)),
        })

    df = pd.DataFrame(rows)
    n_beat = df["beat"].sum()
    med = df["annual_return"].median()
    print(f"      窗口数 {len(df)}, 跑赢沪深300 {n_beat} 个 ({n_beat/len(df):.0%})")
    print(f"      窗口年化中位数 {med:.1f}%  均值 {df['annual_return'].mean():.1f}%")
    print(f"      窗口夏普中位数 {df['sharpe'].median():.2f}")
    print(f"      窗口回撤中位数 {df['max_drawdown'].median():.1f}%")
    print(f"      所有窗口跑赢基准? {'是' if n_beat == len(df) else '否'}")

    csv_file = f"etf_rot_rolling_{timestamp}.csv"
    df.to_csv(csv_file, index=False, encoding="utf-8-sig")
    print(f"      CSV -> {csv_file}")

    print("[3/3] 输出 HTML...")
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=df["start"], y=df["annual_return"], mode="lines+markers",
        name="策略年化", line=dict(color="steelblue")))
    fig.add_trace(go.Scatter(
        x=df["start"], y=df["hs_annual"], mode="lines+markers",
        name="沪深300年化", line=dict(color="coral")))
    fig.add_trace(go.Scatter(
        x=df["start"], y=df["max_drawdown"], mode="lines+markers",
        name="策略回撤", yaxis="y2", line=dict(color="darkred", dash="dash")))
    fig.update_layout(
        title=f"v5 滚动回测 ({timestamp})<br><sup>窗口{WINDOW}日/步{STEP}日 | "
              f"MA200/LB180/R40/TRAIL20%/底仓45% | 全周期 {BT_START} ~ 2026-07</sup>",
        yaxis=dict(title="年化收益 (%)"), yaxis2=dict(title="回撤 (%)", overlaying="y", side="right"),
        height=500,
    )
    # 表格: 窗口明细 (限制显示最近 12 个 + 全窗口汇总)
    df_disp = df.tail(12).copy()
    df_disp["annual"] = df_disp["annual_return"].map(lambda v: f"{v:.1f}%")
    df_disp["dd"] = df_disp["max_drawdown"].map(lambda v: f"{v:.1f}%")
    df_disp["hs"] = df_disp["hs_annual"].map(lambda v: f"{v:.1f}%")
    fig.add_trace(go.Table(
        header=dict(values=["窗口开始", "窗口结束", "年化", "夏普", "回撤", "沪深300", "跑赢", "持仓"]),
        cells=dict(values=[
            df_disp["start"], df_disp["end"], df_disp["annual"], df_disp["sharpe"],
            df_disp["dd"], df_disp["hs"],
            df_disp["beat"].map(lambda b: "✓" if b else "✗"),
            df_disp["holds"],
        ]),
    ))

    html_file = f"etf_rot_rolling_{timestamp}.html"
    fig.write_html(html_file)
    print(f"      HTML -> {html_file}")
    print("\n完成.")


if __name__ == "__main__":
    main()
