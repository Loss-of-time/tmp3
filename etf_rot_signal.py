#!/usr/bin/env python3
"""行业板块 ETF 信号调仓回测 (v7 实验)。

修复 v5 的相位锚定缺陷: v5 用 `i % REBAL == 0` 相对起跑日锚定调仓, 起跑日平移
结果 2.4%~26.7% 天差地别, 26.6% 年化恰是 40 个锚点里的最优解, 期望 alpha 很小。

本版改为**每日检视 + 信号过滤** (参考 research/v5_reports/):
- 西部 CTA: 无固定调仓周期, 只按信号调仓
- 策引: 动量差超过阈值才切换 (避免微小差异频繁调仓)
- 汉斯: 动量得分 > 0 才可入场

规则:
- 每日检查轮动仓: 跌破 MA200 (趋势破坏) 或峰值回撤 TRAIL 离场
- 空仓时: 有 ETF 站上 MA200 且动量 > 0, 买动量最强一只
- 持仓时: 若存在候选动量超过持仓 MOM_GAP 以上, 切换过去 (否则续持)
- 不接飞刀: 距自身 60 日峰值回撤 >= TRAIL 的候选拦停 (等回撤收敛再入)
- 每日检视 => 无相位, 起跑日平移结果不变
- 底仓 BASE_W 恒持底仓标的(BASE_ETF), 剩余做轮动; 空仓吃现金 CASH_APR
"""

import glob
import json
import os
from datetime import datetime

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from backtest import COMMISSION, calc_metrics
from rot_core import (MA_N, LOOKBACK, MOM_GAP, MIN_MOM, TRAIL, COOLDOWN, BASE_W,
                      BASE_ETF, BT_START, CASH_APR, ETFS_DIR, rotation_sim)

HS300_CACHE = "cache_bt/hs300.json"
BENCHES = {"纳指": "cache_bt/ixic.json", "上证": "cache_bt/sh000001.json"}


def load_data():
    etfs = {}
    opens = {}
    for f in sorted(glob.glob(os.path.join(ETFS_DIR, "*.json"))):
        d = json.load(open(f))
        dates = pd.to_datetime(d["dates"])
        etfs[d["code"]] = pd.Series(d["close"], index=dates).sort_index()
        opens[d["code"]] = pd.Series(d["open"], index=dates).sort_index()
    hs300 = json.load(open(HS300_CACHE))
    hs300_s = pd.Series(hs300["close"], index=pd.to_datetime(hs300["dates"])).sort_index()
    return etfs, opens, hs300_s


def load_benches():
    """返回对比基准: {名称: pd.Series} (纳指 + 上证, 替代沪深300)。"""
    out = {}
    for name, path in BENCHES.items():
        d = json.load(open(path))
        out[name] = pd.Series(d["close"], index=pd.to_datetime(d["dates"])).sort_index()
    return out


def rotation_backtest(close_df, open_df=None, bt_start=BT_START, ma_n=MA_N, lookback=LOOKBACK,
                      mom_gap=MOM_GAP, min_mom=MIN_MOM, trail=TRAIL,
                      base_w=BASE_W, base_etf=BASE_ETF, cooldown=COOLDOWN,
                      gate=True):
    """信号调仓回测。返回 (values, trades)。

    成交模型: 今日用昨日收盘信号决策, 今日开盘价成交 (避免前视偏差)。
    open_df 为空时回退用当日收盘成交 (无开盘数据)。
    gate=False 关闭 MA_N 牛熊线(入池门槛+趋势离场), 纯动量+移动止损。
    逐日逻辑在 rot_core.rotation_sim (与模拟盘共用, 防漂移)。
    """
    sim = rotation_sim(close_df, open_df, ma_n=ma_n, lookback=lookback, mom_gap=mom_gap,
                       min_mom=min_mom, trail=trail, cooldown=cooldown, base_w=base_w,
                       base_etf=base_etf, commission=COMMISSION, gate=gate, start=bt_start)
    return pd.Series(sim["navs"], index=sim["dates"]), sim["trades"]


def plot_results(sv, benchs, trades, names, timestamp, title_text=None):
    """benchs: {名称: 归一化Series}"""
    fig = make_subplots(
        rows=3, cols=2,
        subplot_titles=("净值曲线", "年度收益对比", "持仓标的", "策略指标"),
        row_heights=[0.4, 0.3, 0.3],
        specs=[[{"colspan": 2}, None], [{"colspan": 2}, None], [{}, {"type": "table"}]],
    )

    sv = sv / sv.iloc[0]
    fig.add_trace(go.Scatter(x=sv.index, y=sv, mode="lines", name="策略",
                             line=dict(color="steelblue", width=2)), row=1, col=1)
    bench_colors = ["coral", "seagreen"]
    for k, (name, bv) in enumerate(benchs.items()):
        bv = bv / bv.iloc[0]
        fig.add_trace(go.Scatter(x=bv.index, y=bv, mode="lines", name=name,
                                 line=dict(color=bench_colors[k % 2], width=1.5,
                                           dash="dash")), row=1, col=1)

    yr = sv.resample("YE").last()
    sy = yr.pct_change() * 100
    sy.iloc[0] = (yr.iloc[0] / sv.iloc[0] - 1) * 100
    years_labels = [str(d.year) for d in sy.index]
    fig.add_trace(go.Bar(x=years_labels, y=sy.values, name="策略", marker_color="steelblue"), row=2, col=1)
    for k, (name, bv) in enumerate(benchs.items()):
        byr = bv.resample("YE").last()
        by_ = byr.pct_change() * 100
        by_.iloc[0] = (byr.iloc[0] / bv.iloc[0] - 1) * 100
        fig.add_trace(go.Bar(x=years_labels, y=by_.values, name=name,
                             marker_color=bench_colors[k % 2], opacity=0.7), row=2, col=1)

    holds = []
    cur = None
    start = None
    for t in trades:
        if t["action"] in ("buy", "switch"):
            if cur is not None and start is not None:
                holds.append((cur, start, t["date"]))
            cur = t["code"]
            start = t["date"]
        elif t["action"] == "sell":
            if cur is not None and start is not None:
                holds.append((cur, start, t["date"]))
            cur = None
    if cur is not None and start is not None:
        holds.append((cur, start, sv.index[-1]))
    for code, s, e in holds:
        fig.add_trace(go.Scatter(
            x=[s, e], y=[f"{names[code]} {code}", f"{names[code]} {code}"],
            mode="lines", line=dict(width=8), name=f"{names[code]} {code}", showlegend=False,
            hovertext=[f"{names[code]} ({code}): {s.date()} ~ {e.date()}"],
        ), row=3, col=1)

    sm = calc_metrics(sv)
    n_buy = sum(1 for t in trades if t["action"] == "buy")
    n_switch = sum(1 for t in trades if t["action"] == "switch")

    bench_cols = []
    for name, bv in benchs.items():
        bm = calc_metrics(bv)
        bench_cols.append([f"{bm.get('annual_return',0):.1f}", f"{bm.get('sharpe',0):.2f}",
                           f"{bm.get('max_drawdown',0):.1f}", f"{bm.get('total_return',0):.1f}",
                           "-", "-", "-"])
    fig.add_trace(go.Table(
        header=dict(values=["指标", "策略", *[n for n, _ in benchs.items()]]),
        cells=dict(values=[
            ["年化收益 %", "夏普", "最大回撤 %", "总收益 %", "买入次数", "切换次数", "轮动仓"],
            [f"{sm.get('annual_return',0):.1f}", f"{sm.get('sharpe',0):.2f}",
             f"{sm.get('max_drawdown',0):.1f}", f"{sm.get('total_return',0):.1f}",
             f"{n_buy}", f"{n_switch}", f"{int((1-BASE_W)*100)}%"],
            *bench_cols,
        ]),
    ), row=3, col=2)

    if title_text is None:
        title_text = (f"行业ETF信号调仓回测 ({timestamp})<br><sup>"
                      f"每日检视 | MA{MA_N}+动量{LOOKBACK}日 动量差>{MOM_GAP:.0%}才切换 | "
                      f"底仓{BASE_ETF} {int(BASE_W*100)}%+轮动{int((1-BASE_W)*100)}% 止损{int(TRAIL*100)}% | "
                      f"{BT_START} ~ 2026-07</sup>")
    fig.update_layout(
        title_text=title_text,
        height=1000,
    )
    fig.update_yaxes(title_text="净值", row=1, col=1)
    fig.update_yaxes(title_text="年度收益 (%)", row=2, col=1)
    fig.update_yaxes(title_text="持仓标的", row=3, col=1)
    return fig.to_html(include_plotlyjs="cdn", full_html=True)


def main():
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    print("[1/2] 读取缓存...")
    etfs, opens, _ = load_data()
    close_df = pd.DataFrame(etfs).sort_index().ffill()
    open_df = pd.DataFrame(opens).sort_index().reindex(close_df.index).ffill()
    names = {json.load(open(f))["code"]: json.load(open(f))["name"]
             for f in glob.glob(os.path.join(ETFS_DIR, "*.json"))}
    print(f"      {len(etfs)} 只: " + ", ".join(names[c] for c in close_df.columns))

    print(f"[2/2] 回测 (每日检视, MA{MA_N}, 动量{LOOKBACK}日, 动量差>{MOM_GAP:.0%}切换, "
          f"止损{TRAIL:.0%}, 底仓{BASE_W:.0%})...")
    values, trades = rotation_backtest(close_df, open_df=open_df)

    benches = {}
    for name, s in load_benches().items():
        s = s.dropna().reindex(values.index, method="ffill").dropna()
        common = values.dropna().index.intersection(s.index)
        benches[name] = s.reindex(common).ffill() if len(common) else None
    common = values.dropna().index
    sv = values.reindex(common).ffill()
    benches = {k: v for k, v in benches.items() if v is not None}

    sm = calc_metrics(sv)
    n_buy = sum(1 for t in trades if t["action"] == "buy")
    n_switch = sum(1 for t in trades if t["action"] == "switch")

    print("\n--- 回测结果 ---")
    print(f"      策略年化收益: {sm['annual_return']:.1f}%")
    for name, bv in benches.items():
        bm = calc_metrics(bv)
        print(f"      {name}年化收益: {bm['annual_return']:.1f}%")
    print(f"      策略夏普: {sm['sharpe']:.2f}")
    print(f"      策略最大回撤: {sm['max_drawdown']:.1f}%")
    print(f"      总收益: {sm['total_return']:.1f}%")
    print(f"      买入 {n_buy} 次, 切换 {n_switch} 次")

    csv_file = f"etf_rot_signal_result_{timestamp}.csv"
    df_out = {"date": sv.index, "strategy": sv.values}
    for name, bv in benches.items():
        df_out[name] = bv.values
    pd.DataFrame(df_out).to_csv(csv_file, index=False, encoding="utf-8-sig")
    print(f"      CSV -> {csv_file}")

    if trades:
        pd.DataFrame(trades).to_csv(f"etf_rot_signal_trades_{timestamp}.csv",
                                    index=False, encoding="utf-8-sig")
        print(f"      交易明细 -> etf_rot_signal_trades_{timestamp}.csv")

    html_file = f"etf_rot_signal_result_{timestamp}.html"
    with open(html_file, "w", encoding="utf-8") as f:
        f.write(plot_results(sv, benches, trades, names, timestamp))
    print(f"      HTML -> {html_file}")
    print("\n完成.")


if __name__ == "__main__":
    main()
