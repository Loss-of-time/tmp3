#!/usr/bin/env python3
"""v7 策略底仓对比: 红利低波 / 黄金 / 纳指 / 全仓轮动(无底仓)。"""

import json
import os
import glob
from datetime import datetime

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from backtest import calc_metrics
from etf_rot_signal import rotation_backtest, load_data, load_benches, ETFS_DIR

CONFIGS = [
    ("红利低波底仓 45%", dict(base_w=0.45, base_etf="512890"), "#b8860b"),
    ("黄金底仓 45%",     dict(base_w=0.45, base_etf="518880"), "#daa520"),
    ("纳指底仓 45%",     dict(base_w=0.45, base_etf="513100"), "#4682b4"),
    ("全仓轮动 (无底仓)", dict(base_w=0.0, base_etf="518880"), "#2e8b57"),
]


def main():
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    etfs, opens, _ = load_data()
    close_df = pd.DataFrame(etfs).sort_index().ffill()
    open_df = pd.DataFrame(opens).sort_index().reindex(close_df.index).ffill()
    names = {json.load(open(f))["code"]: json.load(open(f))["name"]
             for f in glob.glob(os.path.join(ETFS_DIR, "*.json"))}

    results = {}
    for label, kwargs, _ in CONFIGS:
        sv, trades = rotation_backtest(close_df, open_df=open_df, **kwargs)
        results[label] = {"values": sv.ffill(), "trades": trades}
        m = calc_metrics(results[label]["values"])
        print(f"{label}: 年化{m['annual_return']:.1f}% 夏普{m['sharpe']:.2f} "
              f"回撤{m['max_drawdown']:.1f}% 总收益{m['total_return']:.1f}%")

    common = results[CONFIGS[0][0]]["values"].dropna().index
    common = common.intersection(results[CONFIGS[1][0]]["values"].dropna().index)
    common = common.intersection(results[CONFIGS[2][0]]["values"].dropna().index)
    common = common.intersection(results[CONFIGS[3][0]]["values"].dropna().index)

    benches = {}
    for name, s in load_benches().items():
        s = s.dropna().reindex(common, method="ffill").dropna()
        c = common.intersection(s.index)
        benches[name] = s.reindex(c).ffill()

    fig = make_subplots(
        rows=3, cols=2,
        subplot_titles=("净值曲线 (2015-01 ~ 2026-07)", "年度收益对比", "回撤曲线", "策略指标"),
        row_heights=[0.42, 0.3, 0.28],
        specs=[[{"colspan": 2}, None], [{"colspan": 2}, None], [{}, {"type": "table"}]],
    )

    for label, res in results.items():
        sv = res["values"].reindex(common).ffill() / res["values"].iloc[0]
        fig.add_trace(go.Scatter(x=sv.index, y=sv, mode="lines", name=label,
                                 line=dict(width=2)), row=1, col=1)
    for name, bv in benches.items():
        bv = bv / bv.iloc[0]
        fig.add_trace(go.Scatter(x=bv.index, y=bv, mode="lines", name=name,
                                 line=dict(dash="dot", width=1.5)), row=1, col=1)

    for label, res in results.items():
        sv = res["values"].reindex(common).ffill()
        yr = sv.resample("YE").last()
        sy = yr.pct_change() * 100
        sy.iloc[0] = (yr.iloc[0] / sv.iloc[0] - 1) * 100
        fig.add_trace(go.Bar(x=[str(d.year) for d in sy.index], y=sy.values,
                             name=label, opacity=0.75), row=2, col=1)

    for label, res in results.items():
        sv = res["values"].reindex(common).ffill()
        dd = (sv.cummax() - sv) / sv.cummax() * 100
        fig.add_trace(go.Scatter(x=dd.index, y=dd, mode="lines", name=label,
                                 fill="tozeroy", line=dict(width=1.2)), row=3, col=1)

    rows = []
    for label, res in results.items():
        m = calc_metrics(res["values"].reindex(common).ffill())
        trades = res["trades"]
        n_buy = sum(1 for t in trades if t["action"] == "buy")
        n_switch = sum(1 for t in trades if t["action"] == "switch")
        rows.append([f"{m['annual_return']:.1f}", f"{m['sharpe']:.2f}",
                     f"{m['max_drawdown']:.1f}", f"{m['total_return']:.1f}",
                     f"{n_buy}", f"{n_switch}"])
    rows.append(["-", "-", "-", "-", "-", "-"])
    for name, bv in benches.items():
        bm = calc_metrics(bv)
        rows.append([f"{bm['annual_return']:.1f}", f"{bm['sharpe']:.2f}",
                     f"{bm['max_drawdown']:.1f}", f"{bm['total_return']:.1f}", "-", "-"])

    fig.add_trace(go.Table(
        header=dict(values=["配置", "年化 %", "夏普", "最大回撤 %", "总收益 %", "买入", "切换"]),
        cells=dict(values=[[r[0] for r in rows], [r[1] for r in rows], [r[2] for r in rows],
                           [r[3] for r in rows], [r[4] for r in rows], [r[5] for r in rows]],
                   align="left"),
    ), row=3, col=2)

    fig.update_layout(
        title_text=(f"v7 策略底仓对比 ({timestamp})<br><sup>"
                    f"MA200 | 动量{180}日 | 动量差>1.0%切换 | 止损20% | 底仓45%+轮动55% | 2015-01 ~ 2026-07</sup>"),
        height=1150, legend=dict(orientation="h", y=1.02),
    )
    fig.update_yaxes(title_text="净值", row=1, col=1)
    fig.update_yaxes(title_text="年度收益 (%)", row=2, col=1)
    fig.update_yaxes(title_text="回撤 (%)", row=3, col=1)

    html_file = f"etf_rot_signal_compare_{timestamp}.html"
    with open(html_file, "w", encoding="utf-8") as f:
        f.write(fig.to_html(include_plotlyjs="cdn", full_html=True))
    print(f"HTML -> {html_file}")


if __name__ == "__main__":
    main()
