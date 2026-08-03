#!/usr/bin/env python3
"""ETF 牛熊开关择时回测 (v4)。

策略极简: 每日收盘价 > MA_MA 日均线则持有 ETF (仓位 POS_WEIGHT), 否则空仓
吃现金收益 (CASH_APR)。低频 (约每 4 个月一次信号), 用回撤换安全感。

标的: 沪深300ETF (510300, 前复权 qfq, 数据缓存于 cache_bt/etf510300_qfq.json,
无需网络)。基准: 沪深300指数 (cache_bt/hs300.json)。
回测区间 2020-01 ~ 2026-07, 成本万三双向, 现金年化 2%。
"""

import json
from datetime import datetime

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from backtest import COMMISSION, calc_metrics

# --- config ---
MA_N = 200          # 牛熊均线: 收盘 > MA_N 持仓, 否则空仓
POS_WEIGHT = 0.6    # 持仓权重 (0.6≈半仓, 1.0 满仓)
BT_START = "2020-01-01"
CASH_APR = 0.02

ETF_CACHE = "cache_bt/etf510300_qfq.json"
HS300_CACHE = "cache_bt/hs300.json"


def load_data():
    etf = json.load(open(ETF_CACHE))
    hs300 = json.load(open(HS300_CACHE))
    etf_s = pd.Series(etf["close"], index=pd.to_datetime(etf["dates"])).sort_index()
    hs300_s = pd.Series(hs300["close"], index=pd.to_datetime(hs300["dates"])).sort_index()
    return etf_s, hs300_s


def timing_backtest(close, bt_start=BT_START, ma_n=MA_N, pos_weight=POS_WEIGHT):
    """日线牛熊开关回测。返回 (values, trades). trades 记录每次加/减仓动作。"""
    bt = close[bt_start:]
    ma = bt.rolling(ma_n).mean()
    hold = bt > ma

    values = pd.Series(index=bt.index, dtype=float)
    cash = 1.0
    shares = 0.0
    holding = False
    trades = []

    for i, date in enumerate(bt.index):
        if i == 0:
            values.iloc[i] = cash
            continue

        px = bt.loc[date]
        signal = hold.loc[date]
        if np.isnan(signal):
            signal = holding

        if not holding and signal:
            target = cash * pos_weight
            shares = target / px
            cost = target * COMMISSION
            cash -= target + cost
            holding = True
            trades.append({"date": date, "action": "buy", "price": px, "weight": pos_weight})
        elif holding and not signal:
            proceeds = shares * px * (1 - COMMISSION)
            cash += proceeds
            shares = 0.0
            holding = False
            trades.append({"date": date, "action": "sell", "price": px, "weight": 0.0})

        mv = cash + shares * px
        cash *= (1 + CASH_APR / 252)
        values.iloc[i] = mv

    return values, trades


def plot_results(sv, bv, trades, timestamp):
    fig = make_subplots(
        rows=3, cols=2,
        subplot_titles=("净值曲线", "年度收益对比", "持仓状态与买卖信号", "择时指标"),
        row_heights=[0.4, 0.3, 0.3],
        specs=[[{"colspan": 2}, None], [{"colspan": 2}, None], [{}, {"type": "table"}]],
    )

    sv = sv / sv.iloc[0]
    bv = bv / bv.iloc[0]
    fig.add_trace(go.Scatter(x=sv.index, y=sv, mode="lines", name="策略",
                             line=dict(color="steelblue", width=2)), row=1, col=1)
    fig.add_trace(go.Scatter(x=bv.index, y=bv, mode="lines", name="沪深300",
                             line=dict(color="coral", width=1.5, dash="dash")), row=1, col=1)

    sy = sv.resample("YE").last().pct_change().dropna() * 100
    by_ = bv.resample("YE").last().pct_change().dropna() * 100
    years_labels = [str(d.year) for d in sy.index]
    fig.add_trace(go.Bar(x=years_labels, y=sy.values, name="策略", marker_color="steelblue"), row=2, col=1)
    fig.add_trace(go.Bar(x=years_labels, y=by_.values, name="沪深300", marker_color="coral", opacity=0.7), row=2, col=1)

    # 持仓/空仓状态条
    hold_dates = [d for t in trades for d in (t["date"],)]
    state = pd.Series(1, index=sv.index)
    for t in trades:
        state.loc[t["date"]:] = 1 if t["action"] == "buy" else 0
    fig.add_trace(go.Scatter(x=state.index, y=state * 1.0, mode="lines",
                             line=dict(color="green", width=1), name="持仓(1)/空仓(0)"), row=3, col=1)

    # 买卖信号标记 (绿三角买入, 红三角卖出)
    for t in trades:
        fig.add_trace(go.Scatter(
            x=[t["date"]], y=[1],
            mode="markers",
            marker=dict(color="green" if t["action"] == "buy" else "red",
                        size=9, symbol="triangle-up" if t["action"] == "buy" else "triangle-down"),
            showlegend=False,
        ), row=3, col=1)

    sm = calc_metrics(sv)
    bm = calc_metrics(bv)
    n_buy = sum(1 for t in trades if t["action"] == "buy")
    n_sell = sum(1 for t in trades if t["action"] == "sell")

    fig.add_trace(go.Table(
        header=dict(values=["指标", "策略", "沪深300"]),
        cells=dict(values=[
            ["年化收益 %", "夏普", "最大回撤 %", "总收益 %", "买入次数", "卖出次数", "持仓权重"],
            [f"{sm.get('annual_return',0):.1f}", f"{sm.get('sharpe',0):.2f}",
             f"{sm.get('max_drawdown',0):.1f}", f"{sm.get('total_return',0):.1f}",
             f"{n_buy}", f"{n_sell}", f"{int(POS_WEIGHT*100)}%"],
            [f"{bm.get('annual_return',0):.1f}", f"{bm.get('sharpe',0):.2f}",
             f"{bm.get('max_drawdown',0):.1f}", f"{bm.get('total_return',0):.1f}", "-", "-", "-"],
        ]),
    ), row=3, col=2)

    fig.update_layout(
        title_text=f"沪深300ETF 牛熊开关择时回测 ({timestamp})<br><sup>"
                   f"收盘&gt;MA{MA_N}持仓(权重{POS_WEIGHT}) 否则空仓吃现金{CASH_APR*100:.0f}% | "
                   f"{BT_START} ~ 2026-07</sup>",
        height=1000,
    )
    fig.update_yaxes(title_text="净值", row=1, col=1)
    fig.update_yaxes(title_text="年度收益 (%)", row=2, col=1)
    fig.update_yaxes(title_text="持仓状态", row=3, col=1)
    return fig.to_html(include_plotlyjs="cdn", full_html=True)


def main():
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    print("[1/2] 读取缓存...")
    etf_s, hs300_s = load_data()

    print(f"[2/2] 回测 (MA{MA_N}, 权重{POS_WEIGHT})...")
    values, trades = timing_backtest(etf_s)

    hs300_s = hs300_s.reindex(values.index).ffill()
    common = values.dropna().index.intersection(hs300_s.dropna().index)
    sv = values.reindex(common).ffill()
    bv = hs300_s.reindex(common).ffill()

    sm = calc_metrics(sv)
    bm = calc_metrics(bv)

    print("\n--- 回测结果 ---")
    print(f"      策略年化收益: {sm['annual_return']:.1f}%")
    print(f"      沪深300年化收益: {bm['annual_return']:.1f}%")
    print(f"      策略夏普: {sm['sharpe']:.2f}")
    print(f"      策略最大回撤: {sm['max_drawdown']:.1f}%")
    print(f"      总收益: {sm['total_return']:.1f}%")
    print(f"      买入次数: {sum(1 for t in trades if t['action']=='buy')}")

    csv_file = f"etf_result_{timestamp}.csv"
    pd.DataFrame({"date": sv.index, "strategy": sv.values, "hs300": bv.values}).to_csv(
        csv_file, index=False, encoding="utf-8-sig")
    print(f"      CSV -> {csv_file}")

    if trades:
        tdf = pd.DataFrame(trades)
        tdf.to_csv(f"etf_trades_{timestamp}.csv", index=False, encoding="utf-8-sig")
        print(f"      信号明细 -> etf_trades_{timestamp}.csv")

    html_file = f"etf_result_{timestamp}.html"
    with open(html_file, "w", encoding="utf-8") as f:
        f.write(plot_results(sv, bv, trades, timestamp))
    print(f"      HTML -> {html_file}")
    print("\n完成.")


if __name__ == "__main__":
    main()
