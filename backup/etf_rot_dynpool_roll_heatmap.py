#!/usr/bin/env python3
"""动态池 + 选定参数 (0.8/1.0/45%) 多窗口长度滚动热力图。

x=窗口起点 (季度步进), y=窗口长度 (1/2/3/4/5 年), z=窗口年化。
全周期跑一次再切片 (MA200 热启动), 各窗口独立算年化。
输出 matplotlib PNG (年化热力图 + 超额 vs 纳指) + CSV。
"""
import os
import sys
from datetime import datetime

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
plt.rcParams["font.sans-serif"] = ["Noto Sans CJK SC"]
plt.rcParams["axes.unicode_minus"] = False

from backtest import COMMISSION, calc_metrics
from rot_core import rotation_sim, BASE_ETF, BT_START
from etf_rot_signal import load_benches
from dynpool import load_candidates, fetch_aum, build_pool

STEP = 63             # 季度步进
WINDOWS = [252, 504, 756, 1008, 1260]   # 1/2/3/4/5年
WINDOW_LABELS = ["1年", "2年", "3年", "4年", "5年"]
TS = datetime.now().strftime("%Y%m%d_%H%M%S")


def main():
    print("[1/3] 构建动态池...")
    cands = load_candidates()
    aum = fetch_aum()
    close_df, open_df, log = build_pool(cands, aum)
    admitted = [l for l in log if l[4] == "admit"]
    print(f"      入池 {len(admitted)}/{len(cands)} 只 (AUM {'快照' if aum else '跳过'})")

    print("[2/3] 全周期回测 + 多窗口滚动切片...")
    sim = rotation_sim(close_df, open_df, base_w=0.45, base_etf=BASE_ETF,
                       commission=COMMISSION, start=BT_START,
                       tp_half=0.8, tp_frac=1.0)
    sv = pd.Series(sim["navs"], index=sim["dates"])
    bench = load_benches()["纳指"].reindex(sv.index).ffill()

    rows = []
    for w in WINDOWS:
        for sp in range(0, len(sv) - w + 1, STEP):
            ep = sp + w - 1
            s, e = sv.index[sp], sv.index[ep]
            sm = calc_metrics(sv.loc[s:e])
            bm = calc_metrics(bench.loc[s:e])
            rows.append({"window": w, "start": s.date(), "end": e.date(),
                         "annual": sm["annual_return"], "sharpe": sm["sharpe"],
                         "dd": sm["max_drawdown"], "nd_annual": bm["annual_return"],
                         "alpha": sm["annual_return"] - bm["annual_return"]})
    df = pd.DataFrame(rows)
    csv = f"backup/dynpool_roll_heatmap_{TS}.csv"
    df.to_csv(csv, index=False)
    print(f"      窗口数 {len(df)}, CSV -> {csv}")

    print("[3/3] 画热力图...")
    p_annual = df.pivot(index="window", columns="start", values="annual")
    p_alpha = df.pivot(index="window", columns="start", values="alpha")
    starts = list(p_annual.columns)
    z_annual = p_annual.values
    z_alpha = p_alpha.values
    xs = np.arange(len(starts) + 1)

    def draw(z, title, fname, vmin, vmax, cmap):
        fig, ax = plt.subplots(figsize=(15, 5.5))
        pc = ax.pcolormesh(xs, np.arange(len(WINDOWS) + 1), z,
                           cmap=cmap, vmin=vmin, vmax=vmax, shading="flat")
        for i, row in enumerate(z):
            for j, v in enumerate(row):
                ax.text(j + 0.5, i + 0.5, f"{v:.0f}", ha="center", va="center",
                        fontsize=8.5, color="white" if abs(v - (vmin + vmax) / 2) > (vmax - vmin) * 0.32 else "black")
        step = max(1, len(starts) // 10)
        ax.set_xticks(np.arange(len(starts))[::step] + 0.5)
        ax.set_xticklabels([starts[i].strftime("%y-%m") for i in range(0, len(starts), step)], fontsize=9)
        ax.set_yticks(np.arange(len(WINDOWS)) + 0.5)
        ax.set_yticklabels(WINDOW_LABELS)
        ax.set_xlabel("窗口起点")
        ax.set_title(f"{title} ({TS})")
        fig.subplots_adjust(right=0.88)
        cb = fig.add_axes([0.90, 0.12, 0.02, 0.72])
        fig.colorbar(pc, cax=cb, label="年化 (%)")
        png = f"backup/{fname}_{TS}.png"
        fig.savefig(png, dpi=150)
        print(f"      PNG -> {png}")

    draw(z_annual, "动态池 0.8/1.0/45% 滚动窗口年化", "dynpool_roll_heatmap", -20, 60, "RdYlGn")
    draw(z_alpha, "滚动窗口超额年化 (策略 - 纳指)", "dynpool_roll_alpha_heatmap", -30, 30, "RdBu_r")


if __name__ == "__main__":
    main()
