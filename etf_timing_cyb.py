#!/usr/bin/env python3
"""创业板ETF 牛熊开关 + 峰值止损回测 (v4 创业板版)。

策略: 收盘 > MA_MA 均线则满仓持有创业板ETF (159915), 跌破均线或自持仓
峰值回撤 >= TRAIL 则清仓吃现金 (CASH_APR)。震荡市反复被洗小亏,
牛市全程持有吃翻倍段 —— 检验"多数小亏、少数大赚"形态。

可选: BASE_W 比例资金恒持红利低波 (512890) 作底仓, 稀释组合回撤 (v5 思想)。

标的: 创业板ETF (159915, 前复权 qfq, cache_bt/etf159915.json)。
基准: 159915 买入持有。回测 2013-01 起 (带底仓时受 512890 限制 2019-01 起),
成本万三双向, 现金年化 2%。
"""

import json
from datetime import datetime

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from backtest import COMMISSION, calc_metrics

# --- config ---
MA_N = 300          # 牛熊均线: 收盘 > MA_N 持仓, 否则空仓 (扫参 100~500, 300 最优)
POS_WEIGHT = 1.0    # 持仓权重 (满仓)
TRAIL = 0.20        # 峰值回撤止损: 持仓中自峰值回撤 >= TRAIL 即清仓 (0 关闭)
BASE_W = 0.45       # 红利低波底仓权重 (0 关闭, v5 思想稀释回撤)
BT_START = "2013-01-01"
CASH_APR = 0.02

ETF_CACHE = "cache_bt/etf159915.json"
BASE_CACHE = "cache_bt/etf_industry/512890.json"
HS300_CACHE = "cache_bt/hs300.json"


def load_data():
    etf = json.load(open(ETF_CACHE))
    etf_s = pd.Series(etf["close"], index=pd.to_datetime(etf["dates"])).sort_index()
    base = None
    if BASE_W > 0:
        b = json.load(open(BASE_CACHE))
        base = pd.Series(b["close"], index=pd.to_datetime(b["dates"])).sort_index()
    return etf_s, base


def timing_backtest(close, base_close=None, base_w=BASE_W, bt_start=BT_START,
                    ma_n=MA_N, pos_weight=POS_WEIGHT, trail=TRAIL):
    """日线牛熊开关回测 (可选恒持红利低波底仓)。返回 (values, trades)。"""
    bt = close[bt_start:]
    ma = bt.rolling(ma_n).mean()
    hold = bt > ma

    if base_close is not None and base_w > 0:
        base = base_close.reindex(bt.index).ffill()
        base_shares = base_w * (1 - COMMISSION) / base.iloc[0]
    else:
        base, base_shares = None, 0.0

    values = pd.Series(index=bt.index, dtype=float)
    cash = 1.0 - base_w
    shares = 0.0
    holding = False
    peak = 0.0
    buy_px = 0.0
    trades = []

    for i, date in enumerate(bt.index):
        if i == 0:
            values.iloc[i] = cash + base_shares * (base.iloc[0] if base is not None else 0)
            continue

        px = bt.loc[date]
        signal = hold.loc[date]
        if np.isnan(signal):
            signal = holding
        if holding:
            peak = max(peak, px)
        if trail > 0 and holding and px <= peak * (1 - trail):
            signal = False

        if not holding and signal:
            target = cash * pos_weight
            shares = target / px
            cost = target * COMMISSION
            cash -= target + cost
            holding = True
            peak = px
            buy_px = px
            trades.append({"date": date, "action": "buy", "price": px, "weight": pos_weight})
        elif holding and not signal:
            proceeds = shares * px * (1 - COMMISSION)
            cash += proceeds
            shares = 0.0
            holding = False
            trades.append({"date": date, "action": "sell", "price": px, "weight": 0.0,
                           "pnl": px / buy_px - 1})

        mv = cash + shares * px
        if base is not None:
            mv += base_shares * base.loc[date]
        cash *= (1 + CASH_APR / 252)
        values.iloc[i] = mv

    return values, trades


def plot_results(sv, bv, trades, timestamp):
    fig = make_subplots(
        rows=3, cols=2,
        subplot_titles=("净值曲线", "年度收益对比", "持仓状态与买卖信号", "指标与交易分布"),
        row_heights=[0.4, 0.3, 0.3],
        specs=[[{"colspan": 2}, None], [{"colspan": 2}, None], [{}, {"type": "table"}]],
    )

    sv = sv / sv.iloc[0]
    bv = bv / bv.iloc[0]
    fig.add_trace(go.Scatter(x=sv.index, y=sv, mode="lines", name="策略",
                             line=dict(color="steelblue", width=2)), row=1, col=1)
    fig.add_trace(go.Scatter(x=bv.index, y=bv, mode="lines", name="159915买入持有",
                             line=dict(color="coral", width=1.5, dash="dash")), row=1, col=1)

    sy = sv.resample("YE").last().pct_change().dropna() * 100
    by_ = bv.resample("YE").last().pct_change().dropna() * 100
    years_labels = [str(d.year) for d in sy.index]
    fig.add_trace(go.Bar(x=years_labels, y=sy.values, name="策略", marker_color="steelblue"), row=2, col=1)
    fig.add_trace(go.Bar(x=years_labels, y=by_.values, name="159915买入持有", marker_color="coral", opacity=0.7), row=2, col=1)

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
    pnls = [t["pnl"] for t in trades if t["action"] == "sell"]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    n_double = sum(1 for p in pnls if p >= 1.0)
    dist = (
        f"{len(pnls)}笔 | 赢{len(wins)}/亏{len(losses)} | "
        f"均赢{np.mean(wins)*100:.0f}% 均亏{np.mean(losses)*100:.0f}% | "
        f"最大单笔{max(pnls)*100:.0f}% | 翻倍{n_double}笔"
    )

    fig.add_trace(go.Table(
        header=dict(values=["指标", "策略", "159915买入持有"]),
        cells=dict(values=[
            ["年化收益 %", "夏普", "最大回撤 %", "总收益 %", "买入次数", "卖出次数", "交易分布"],
            [f"{sm.get('annual_return',0):.1f}", f"{sm.get('sharpe',0):.2f}",
             f"{sm.get('max_drawdown',0):.1f}", f"{sm.get('total_return',0):.1f}",
             f"{n_buy}", f"{n_sell}", dist],
            [f"{bm.get('annual_return',0):.1f}", f"{bm.get('sharpe',0):.2f}",
             f"{bm.get('max_drawdown',0):.1f}", f"{bm.get('total_return',0):.1f}", "-", "-", "-"],
        ]),
    ), row=3, col=2)

    fig.update_layout(
        title_text=f"创业板ETF 牛熊开关+峰值止损回测 ({timestamp})<br><sup>"
                   f"收盘&gt;MA{MA_N}满仓, 跌破或峰值回撤&gt;={TRAIL*100:.0f}%清仓吃现金{CASH_APR*100:.0f}% | "
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
    etf_s, base = load_data()

    bt_start = BT_START if base is None else str(base.index[0].date())
    print(f"[2/2] 回测 (MA{MA_N}, 满仓, TRAIL={TRAIL}, 底仓{BASE_W:.0%})...")
    values, trades = timing_backtest(etf_s, base, bt_start=bt_start)

    etf_s = etf_s.reindex(values.index).ffill()
    common = values.dropna().index.intersection(etf_s.dropna().index)
    sv = values.reindex(common).ffill()
    bv = etf_s.reindex(common).ffill()

    sm = calc_metrics(sv)
    bm = calc_metrics(bv)

    # 无底仓对照组 (同区间)
    if BASE_W > 0:
        v0, _ = timing_backtest(etf_s, None, base_w=0.0, bt_start=bt_start)
        m0 = calc_metrics(v0.reindex(common).ffill())
        print(f"      无底仓对照: 年化{m0['annual_return']:.1f}% 回撤{m0['max_drawdown']:.1f}% 夏普{m0['sharpe']:.2f}")

    pnls = [t["pnl"] for t in trades if t["action"] == "sell"]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]

    print("\n--- 回测结果 ---")
    print(f"      策略年化收益: {sm['annual_return']:.1f}%  (买入持有: {bm['annual_return']:.1f}%)")
    print(f"      策略夏普: {sm['sharpe']:.2f}")
    print(f"      策略最大回撤: {sm['max_drawdown']:.1f}%")
    print(f"      总收益: {sm['total_return']:.1f}%")
    print(f"      交易分布: {len(pnls)}笔, 赢{len(wins)}/亏{len(losses)}, "
          f"均赢{np.mean(wins)*100:.0f}%, 均亏{np.mean(losses)*100:.0f}%, "
          f"最大单笔{max(pnls)*100:.0f}%, 翻倍{sum(1 for p in pnls if p>=1.0)}笔")

    csv_file = f"etf_cyb_result_{timestamp}.csv"
    pd.DataFrame({"date": sv.index, "strategy": sv.values, "buyhold": bv.values}).to_csv(
        csv_file, index=False, encoding="utf-8-sig")
    print(f"      CSV -> {csv_file}")

    if trades:
        tdf = pd.DataFrame(trades)
        tdf.to_csv(f"etf_cyb_trades_{timestamp}.csv", index=False, encoding="utf-8-sig")
        print(f"      信号明细 -> etf_cyb_trades_{timestamp}.csv")

    html_file = f"etf_cyb_result_{timestamp}.html"
    with open(html_file, "w", encoding="utf-8") as f:
        f.write(plot_results(sv, bv, trades, timestamp))
    print(f"      HTML -> {html_file}")
    print("\n完成.")


if __name__ == "__main__":
    main()
