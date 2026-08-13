#!/usr/bin/env python3
"""止跌策略 (trail=0.20 叠加 drop 20d8%) vs 基线 (纯 trail) 3年滚动稳定性验证 (2026-08-07)。

窗口 756 日逐季度滑动, 全周期跑一次再切片 (MA 热启动), 各窗口独立算年化/夏普/回撤,
与同期纳指对比。输出 CSV + 对比 PNG。
"""
import os
import sys
from datetime import datetime

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
plt.rcParams["font.sans-serif"] = ["Noto Sans CJK SC"]
plt.rcParams["axes.unicode_minus"] = False

from backtest import COMMISSION, calc_metrics
from rot_core import rotation_sim, BASE_ETF, BT_START
from etf_rot_signal import load_data, load_benches

WINDOW = 756          # 3年窗口
STEP = 63             # 季度步进
TS = datetime.now().strftime("%Y%m%d_%H%M%S")
VARIANTS = [("基线 trail", dict(trail=0.20)),
            ("叠加 20d8%", dict(trail=0.20, drop_n=20, drop_x=0.08))]


def main():
    print("[1/2] 全周期回测 + 3年滚动切片...")
    close_df, open_df, _, tradable = load_data()
    bench = load_benches()["纳指"]
    rows = []
    for name, kw in VARIANTS:
        sim = rotation_sim(close_df, open_df, base_w=0.45, base_etf=BASE_ETF,
                           commission=COMMISSION, start=BT_START,
                           tp_half=0.8, tp_frac=1.0, tradable=tradable, **kw)
        sv = pd.Series(sim["navs"], index=sim["dates"])
        b = bench.reindex(sv.index).ffill()
        for sp in range(0, len(sv) - WINDOW + 1, STEP):
            ep = sp + WINDOW - 1
            s, e = sv.index[sp], sv.index[ep]
            sm = calc_metrics(sv.loc[s:e])
            bm = calc_metrics(b.loc[s:e])
            rows.append({"variant": name, "start": s.date(), "end": e.date(),
                         "annual": sm["annual_return"], "sharpe": sm["sharpe"],
                         "dd": sm["max_drawdown"], "nd_annual": bm["annual_return"],
                         "beat": sm["annual_return"] > bm["annual_return"]})
    df = pd.DataFrame(rows)
    for name, _ in VARIANTS:
        sub = df[df.variant == name]
        nbeat = sub["beat"].sum()
        print(f"\n== {name}: 窗口数 {len(sub)}, 跑赢纳指 {nbeat} ({nbeat/len(sub):.0%})")
        print(f"  窗口年化 中位 {sub['annual'].median():.1f}% 均值 {sub['annual'].mean():.1f}% "
              f"最差 {sub['annual'].min():.1f}% 最好 {sub['annual'].max():.1f}%")
        print(f"  窗口回撤 中位 {sub['dd'].median():.1f}% 最大 {sub['dd'].max():.1f}% "
              f"夏普中位 {sub['sharpe'].median():.2f}  纳指中位 {sub['nd_annual'].median():.1f}%")

    csv = f"backup/drop_roll3_{TS}.csv"
    df.to_csv(csv, index=False)
    print(f"\nCSV -> {csv}")

    print("[2/2] 画图...")
    fig, ax = plt.subplots(figsize=(12, 5.5))
    for name, color in zip([v[0] for v in VARIANTS], ["steelblue", "darkorange"]):
        sub = df[df.variant == name]
        ax.plot(sub["start"], sub["annual"], "o-", color=color, label=f"{name} 窗口年化")
    ax.plot(df[df.variant == VARIANTS[0][0]]["start"],
            df[df.variant == VARIANTS[0][0]]["nd_annual"], "--", color="gray", label="纳指同期年化")
    ax.axhline(0, color="black", lw=0.6)
    ax.set_ylabel("窗口年化 (%)")
    ax2 = ax.twinx()
    for name, color in zip([v[0] for v in VARIANTS], ["tomato", "darkred"]):
        sub = df[df.variant == name]
        ax2.plot(sub["start"], sub["dd"], "s--", color=color, lw=0.8, alpha=0.7,
                 label=f"{name} 回撤")
    ax2.set_ylabel("窗口回撤 (%)")
    ax.legend(loc="upper left")
    ax2.legend(loc="lower left")
    ax.set_title(f"止跌叠加 vs 基线 三年滚动 ({TS})")
    fig.tight_layout()
    png = f"backup/drop_roll3_{TS}.png"
    fig.savefig(png, dpi=150)
    print(f"PNG -> {png}")


if __name__ == "__main__":
    main()
