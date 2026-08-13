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

import json
import os
from datetime import datetime

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from backtest import COMMISSION, calc_metrics
from rot_core import (MA_N, LOOKBACK, MOM_GAP, MIN_MOM, TRAIL, COOLDOWN, BASE_W,
                      BASE_ETF, BT_START, CASH_APR, rotation_sim, TP_HALF, TP_FRAC,
                      DROP_N, DROP_X)

BENCHES = {"纳指": "cache_bt/ixic.json", "上证": "cache_bt/sh000001.json"}


def load_data():
    """默认动态池: 候选=行业池+explore, 规则化入池 (上市满3年/AUM≥10亿/相关<0.8/只进不出)。

    AUM 拉取失败降级为跳过 (离线可跑)。返回 (close_df, open_df, names)。
    """
    from dynpool import load_candidates, fetch_aum, build_pool
    cands = load_candidates()
    close_df, open_df, _, tradable = build_pool(cands, fetch_aum())
    names = {c: d["name"] for c, d in cands.items()}
    return close_df, open_df, names, tradable


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
                      gate=True, tradable=None, tp_half=TP_HALF, tp_frac=TP_FRAC,
                      drop_n=DROP_N, drop_x=DROP_X):
    """信号调仓回测。返回 (values, trades)。

    成交模型: 今日用昨日收盘信号决策, 今日开盘价成交 (避免前视偏差)。
    open_df 为空时回退用当日收盘成交 (无开盘数据)。
    gate=False 关闭 MA_N 牛熊线(入池门槛+趋势离场), 纯动量+移动止损。
    逐日逻辑在 rot_core.rotation_sim (与模拟盘共用, 防漂移)。
    """
    sim = rotation_sim(close_df, open_df, ma_n=ma_n, lookback=lookback, mom_gap=mom_gap,
                       min_mom=min_mom, trail=trail, cooldown=cooldown, base_w=base_w,
                       base_etf=base_etf, commission=COMMISSION, gate=gate, start=bt_start,
                       tradable=tradable, tp_half=tp_half, tp_frac=tp_frac,
                       drop_n=drop_n, drop_x=drop_x)
    return pd.Series(sim["navs"], index=sim["dates"]), sim["trades"]


def plot_results(sv, benchs, trades, names, timestamp, title_text=None, params=None):
    """benchs: {名称: 归一化Series}; params: 实际运行参数 dict, 标题据此渲染"""
    fig = make_subplots(
        rows=2, cols=2,
        subplot_titles=("净值(对数) + 持仓", "年度收益对比", "策略指标"),
        row_heights=[0.58, 0.42],
        specs=[[{"colspan": 2}, None], [{}, {"type": "table"}]],
    )

    sv = sv / sv.iloc[0]
    fig.add_trace(go.Scatter(x=sv.index, y=sv, mode="lines", name="策略",
                             line=dict(color="steelblue", width=2)), row=1, col=1)
    bench_colors = ["coral", "seagreen"]
    for k, (name, bv) in enumerate(benchs.items()):
        bv = bv.loc[bv.index >= sv.index[0]]      # 基准裁剪到回测起点, 防前导段
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
    row_names = []
    for code, _, _ in holds:
        nm = f"{names[code]} {code}"
        if nm not in row_names:
            row_names.append(nm)
    palette = ["steelblue", "coral", "seagreen", "goldenrod", "mediumpurple",
               "tomato", "dodgerblue", "chocolate", "teal", "hotpink",
               "olive", "indianred", "darkcyan", "plum", "darkorange"]
    row_color = {nm: palette[i % len(palette)] for i, nm in enumerate(row_names)}
    buy_nav = [float(sv.loc[s]) for _, s, _ in holds]
    for (code, s, e), bn in zip(holds, buy_nav):
        nm = f"{names[code]} {code}"
        fig.add_trace(go.Scatter(
            x=[s, e], y=[bn, bn],
            mode="lines", line=dict(width=4, color=row_color[nm]), name=nm, showlegend=False,
            hovertext=[f"{names[code]} ({code}): {s.date()} ~ {e.date()} 买入净值 {bn:.2f}",
                       f"{names[code]} ({code}): {s.date()} ~ {e.date()} 买入净值 {bn:.2f}"],
            xaxis="x", yaxis="y",
        ))
    for i in range(len(holds) - 1):
        if holds[i][2] == holds[i + 1][1]:
            fig.add_trace(go.Scatter(
                x=[holds[i][2], holds[i][2]], y=[buy_nav[i], buy_nav[i + 1]],
                mode="lines", line=dict(color="rgba(128,128,128,0.7)", width=1.5, dash="dot"),
                showlegend=False, hoverinfo="skip", xaxis="x", yaxis="y",
            ))

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
    ), row=2, col=2)

    if title_text is None:
        p = dict(ma_n=MA_N, lookback=LOOKBACK, mom_gap=MOM_GAP, min_mom=MIN_MOM,
                 trail=TRAIL, base_w=BASE_W, base_etf=BASE_ETF, cooldown=COOLDOWN,
                 tp_half=TP_HALF, tp_frac=TP_FRAC, drop_n=DROP_N, drop_x=DROP_X, bt_start=BT_START)
        p.update(params or {})
        parts = [f"每日检视 | MA{p['ma_n']}+动量{p['lookback']}日 动量差>{p['mom_gap']:.0%}才切换",
                 f"底仓{p['base_etf']} {p['base_w']:.0%}+轮动{1-p['base_w']:.0%}",
                 f"冷却{p['cooldown']}日"]
        if p["trail"] is not None:
            parts.append(f"峰值止损{p['trail']:.0%}")
        if p["drop_n"]:
            parts.append(f"止跌{p['drop_n']}d{p['drop_x']:.0%}")
        if p["tp_half"]:
            parts.append(f"止盈浮盈{p['tp_half']:.0%}卖{p['tp_frac']:.0%}")
        title_text = (f"行业ETF信号调仓回测 ({timestamp})<br><sup>"
                      f"{' | '.join(parts)} | {p['bt_start']} ~ "
                      f"{sv.index[-1].strftime('%Y-%m')}</sup>")
    span_days = (sv.index[-1] - sv.index[0]).days
    if span_days <= 60:
        xaxis = dict(dtick="D1", tickformat="%Y-%m-%d")
    elif span_days <= 800:
        xaxis = dict(dtick="M1", tickformat="%Y-%m")
    else:
        xaxis = dict(tickmode="linear", dtick="M12", tickformat="%Y")
    fig.update_layout(
        title_text=title_text,
        height=1400,
        yaxis=dict(type="log", title="净值(对数)"),
        xaxis=xaxis,
    )
    fig.update_yaxes(title_text="年度收益 (%)", row=2, col=1)
    return fig.to_html(include_plotlyjs="cdn", full_html=True)


def main():
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    print("[1/2] 读取缓存 + 构建动态池...")
    close_df, open_df, names, tradable = load_data()
    print(f"      池 {len(close_df.columns)} 只: " + ", ".join(names[c] for c in close_df.columns))

    print(f"[2/2] 回测 (每日检视, MA{MA_N}, 动量{LOOKBACK}日, 动量差>{MOM_GAP:.0%}切换, "
          f"止损{TRAIL:.0%}, 底仓{BASE_W:.0%})...")
    values, trades = rotation_backtest(close_df, open_df=open_df, tradable=tradable)
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
    params = dict(ma_n=MA_N, lookback=LOOKBACK, mom_gap=MOM_GAP, min_mom=MIN_MOM,
                  trail=TRAIL, base_w=BASE_W, base_etf=BASE_ETF, cooldown=COOLDOWN,
                  tp_half=TP_HALF, tp_frac=TP_FRAC, drop_n=DROP_N, drop_x=DROP_X, bt_start=BT_START)
    with open(html_file, "w", encoding="utf-8") as f:
        f.write(plot_results(sv, benches, trades, names, timestamp, params=params))
    print(f"      HTML -> {html_file}")
    print("\n完成.")


if __name__ == "__main__":
    main()
