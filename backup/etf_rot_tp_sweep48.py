#!/usr/bin/env python3
"""v7 止盈二维调优 (2026-08-07, 48只动态池新口径): TP_HALF × TP_FRAC 网格。

背景: 定稿 0.8/1.0 来自 55 只旧口径 (etf_rot_risk3.py), 池修复后重扫确认平台性。
双窗验证: 官方窗 2016-08-11 + 2020 窗。多进程并行。
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import concurrent.futures as cf

import pandas as pd

from backtest import COMMISSION, calc_metrics
from etf_rot_signal import load_data
from rot_core import rotation_sim, BASE_ETF, BASE_W

WINDOWS = [("官方", "2016-08-11"), ("2020", "2020-01-01")]
TP_HALFS = [None, 0.4, 0.5, 0.6, 0.7, 0.8, 1.0, 1.2, 1.5]
TP_FRACS = [0.25, 0.5, 0.75, 1.0]
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                   "tp_sweep48.csv")


def run(args):
    wstart, th, f, close_df, open_df, tradable = args
    sim = rotation_sim(close_df, open_df, base_w=BASE_W, base_etf=BASE_ETF,
                       commission=COMMISSION, start=wstart,
                       tp_half=th, tp_frac=f, tradable=tradable)
    sv = pd.Series(sim["navs"], index=sim["dates"])
    m = calc_metrics(sv)
    nh = sum(1 for t in sim["trades"] if t["action"] == "tp_half")
    return {"window": wstart, "tp_half": th, "tp_frac": f,
            "ann": m["annual_return"], "sharpe": m["sharpe"],
            "dd": m["max_drawdown"], "total": m["total_return"],
            "trades": len(sim["trades"]), "nh": nh}


def main():
    close_df, open_df, _, tradable = load_data()
    combos = [(ws, th, f) for _, ws in WINDOWS
              for th in TP_HALFS for f in TP_FRACS]
    args = [(ws, th, f, close_df, open_df, tradable) for ws, th, f in combos]
    rows = []
    with cf.ProcessPoolExecutor(max_workers=12) as ex:
        for r in ex.map(run, args):
            rows.append(r)
    df = pd.DataFrame(rows)
    df.to_csv(OUT, index=False)
    for ws, _ in WINDOWS:
        sub = df[df.window == ws].pivot_table(index="tp_half", columns="tp_frac",
                                              values="ann")
        sub2 = df[df.window == ws].pivot_table(index="tp_half", columns="tp_frac",
                                               values="sharpe")
        print(f"\n== {ws} 年化 (tp_half × tp_frac) ==")
        print(sub.round(3).to_string())
        print(f"== {ws} 夏普 ==")
        print(sub2.round(2).to_string())
    print(f"\nsaved {OUT} ({len(df)} rows)")


if __name__ == "__main__":
    main()
