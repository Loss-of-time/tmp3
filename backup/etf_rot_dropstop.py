#!/usr/bin/env python3
"""v7 下跌速度止损实验 (2026-08-07): 用 N 日跌幅代替/叠加 20% 峰值回撤止损。

用户提议: 固定 20% 峰值回撤 (trail) 与下跌速度无关, 跌得快应早离场。
实现: rotation_sim 新增 drop_n/drop_x (收盘较 N 日前跌 >= x 即离场, reason="drop")。
对比: 基线 trail=0.20; trail=None 纯 drop; trail=0.20 + drop 叠加。双窗。
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import concurrent.futures as cf

import pandas as pd

from backtest import COMMISSION, calc_metrics
from etf_rot_signal import load_data
from rot_core import rotation_sim, BASE_ETF, BASE_W

WINDOWS = [("官方", "2016-08-11"), ("2020", "2020-01-01")]
DROPS = [(5, 0.05), (5, 0.08), (5, 0.10), (10, 0.05), (10, 0.08), (10, 0.10),
         (10, 0.15), (20, 0.08), (20, 0.10), (20, 0.15), (30, 0.10), (30, 0.15)]
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                   "dropstop_sweep.csv")


def run(args):
    wstart, trail, drop_n, drop_x, close_df, open_df, tradable = args
    sim = rotation_sim(close_df, open_df, base_w=BASE_W, base_etf=BASE_ETF,
                       commission=COMMISSION, start=wstart,
                       trail=trail, tp_half=0.8, tp_frac=1.0,
                       drop_n=drop_n, drop_x=drop_x, tradable=tradable)
    sv = pd.Series(sim["navs"], index=sim["dates"])
    m = calc_metrics(sv)
    nd = sum(1 for t in sim["trades"] if t["action"] == "sell" and t["reason"] == "drop")
    return {"window": wstart, "trail": trail, "drop_n": drop_n, "drop_x": drop_x,
            "ann": m["annual_return"], "sharpe": m["sharpe"],
            "dd": m["max_drawdown"], "total": m["total_return"],
            "trades": len(sim["trades"]), "nd": nd}


def main():
    close_df, open_df, _, tradable = load_data()
    combos = []
    for _, ws in WINDOWS:
        combos.append((ws, 0.20, 0, 0.0))                    # 基线
        for n, x in DROPS:
            combos.append((ws, None, n, x))                  # 纯 drop 替代
            combos.append((ws, 0.20, n, x))                  # 叠加
    args = [(ws, tr, n, x, close_df, open_df, tradable)
            for ws, tr, n, x in combos]
    rows = []
    with cf.ProcessPoolExecutor(max_workers=12) as ex:
        for r in ex.map(run, args):
            rows.append(r)
    df = pd.DataFrame(rows)
    df.to_csv(OUT, index=False)
    for ws, _ in WINDOWS:
        sub = df[df.window == ws]
        base = sub[sub.drop_n == 0].iloc[0]
        print(f"\n== {ws} 基线: trail=0.20 年化{base.ann:.2f} 夏普{base.sharpe:.2f} "
              f"回撤{base.dd:.1f}% ==")
        print(f"{'模式':<6}{'drop':>8} {'年化':>7} {'夏普':>6} {'回撤':>7} "
              f"{'总收益':>8} {'成交':>5} {'drop卖':>5}")
        print(f"{'---':<6}{'---':>8} {'---':>7} {'---':>6} {'---':>7} "
              f"{'---':>8} {'---':>5} {'---':>5}")
        for _, r in sub.iterrows():
            if r.drop_n == 0:
                continue
            mode = "替代" if pd.isna(r.trail) else "叠加"
            print(f"{mode:<6}{r.drop_n}d{r.drop_x:.0%} {'':>3} {r.ann:7.2f} "
                  f"{r.sharpe:6.2f} {r.dd:7.1f} {r.total:8.1f} {r.trades:5d} {r.nd:5d}")
    print(f"\nsaved {OUT} ({len(df)} rows)")


if __name__ == "__main__":
    main()
