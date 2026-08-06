#!/usr/bin/env python3
"""动态池 + 选定参数 (tp_half=0.8 / tp_frac=1.0 / base_w=0.45) 的 3 年滚动窗口稳定性验证。

窗口 756 日 (3年) 逐季度滑动, 策略状态连续 (全周期跑一次再切片, MA200 热启动),
各窗口独立算年化/夏普/回撤, 与同期纳指对比。动态池规则同 etf_rot_dynpool:
上市满3年 / AUM≥10亿(当前快照近似) / 相关<0.8 / 只进不出。
输出 CSV + matplotlib 曲线 PNG。
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
from etf_rot_signal import load_benches
from etf_rot_dynpool import load_candidates, fetch_aum, build_pool

WINDOW = 756          # 3年窗口
STEP = 63             # 季度步进
TS = datetime.now().strftime("%Y%m%d_%H%M%S")


def main():
    print("[1/3] 构建动态池...")
    cands = load_candidates()
    aum = fetch_aum()
    close_df, open_df, log = build_pool(cands, aum)
    admitted = [l for l in log if l[4] == "admit"]
    print(f"      入池 {len(admitted)}/{len(cands)} 只 (AUM {'快照' if aum else '跳过'})")

    print("[2/3] 全周期回测 (选定参数) + 3年滚动切片...")
    sim = rotation_sim(close_df, open_df, base_w=0.45, base_etf=BASE_ETF,
                       commission=COMMISSION, start=BT_START,
                       tp_half=0.8, tp_frac=1.0)
    sv = pd.Series(sim["navs"], index=sim["dates"])
    bench = load_benches()["纳指"].reindex(sv.index).ffill()

    rows = []
    for sp in range(0, len(sv) - WINDOW + 1, STEP):
        ep = sp + WINDOW - 1
        s, e = sv.index[sp], sv.index[ep]
        sm = calc_metrics(sv.loc[s:e])
        bm = calc_metrics(bench.loc[s:e])
        rows.append({"start": s.date(), "end": e.date(),
                     "annual": sm["annual_return"], "sharpe": sm["sharpe"],
                     "dd": sm["max_drawdown"], "nd_annual": bm["annual_return"],
                     "beat": sm["annual_return"] > bm["annual_return"]})
    df = pd.DataFrame(rows)
    nbeat = df["beat"].sum()
    print(f"      窗口数 {len(df)}, 跑赢纳指 {nbeat} ({nbeat/len(df):.0%})")
    print(f"      窗口年化 中位 {df['annual'].median():.1f}%  均值 {df['annual'].mean():.1f}%  "
          f"最差 {df['annual'].min():.1f}% 最好 {df['annual'].max():.1f}%")
    print(f"      窗口回撤 中位 {df['dd'].median():.1f}%  最大 {df['dd'].max():.1f}%  "
          f"夏普中位 {df['sharpe'].median():.2f}")
    print(f"      纳指窗口年化 中位 {df['nd_annual'].median():.1f}%")

    csv = f"backup/dynpool_roll3_{TS}.csv"
    df.to_csv(csv, index=False)
    print(f"      CSV -> {csv}")

    print("[3/3] 画图...")
    fig, ax = plt.subplots(figsize=(12, 5.5))
    ax.plot(df["start"], df["annual"], "o-", color="steelblue", label="策略窗口年化")
    ax.plot(df["start"], df["nd_annual"], "--", color="gray", label="纳指同期年化")
    ax.axhline(0, color="black", lw=0.6)
    ax.set_ylabel("窗口年化 (%)")
    ax2 = ax.twinx()
    ax2.plot(df["start"], df["dd"], "s--", color="darkred", lw=0.8, alpha=0.7, label="窗口回撤")
    ax2.set_ylabel("窗口回撤 (%)")
    ax.legend(loc="upper left")
    ax2.legend(loc="lower left")
    ax.set_title(f"动态池 + 0.8/1.0/45% 三年滚动窗口 ({TS}) | 跑赢纳指 {nbeat}/{len(df)}")
    fig.tight_layout()
    png = f"backup/dynpool_roll3_{TS}.png"
    fig.savefig(png, dpi=150)
    print(f"      PNG -> {png}")


if __name__ == "__main__":
    main()
