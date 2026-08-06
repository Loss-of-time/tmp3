#!/usr/bin/env python3
"""v7 三风险参数联合调优 (2026-08-06): 止盈阈值 TP_HALF × 止盈卖出占比 TP_FRAC × 底仓 BASE_W。

背景: TP_HALF=0.8 已单独定稿 (etf_rot_tphalf.py), 但 TP_FRAC 从未参数化
(代码硬编码 0.5), BASE_W 与止盈的交互未知。本脚本做 3D 网格联合扫描 +
双窗 (2015/2020) 稳定性验证: 最优组合需双窗同向 + 邻域平坦 (非单点尖峰)。
结果缓存至 CSV, 并行计算。
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import concurrent.futures as cf
import itertools

import pandas as pd

from backtest import COMMISSION, calc_metrics
from etf_rot_signal import load_data
from rot_core import rotation_sim, BASE_ETF

WINDOWS = [("2015起", "2015-01-01"), ("2020起", "2020-01-01")]
TP_HALFS = [None, 0.5, 0.8, 1.0, 1.5]
TP_FRACS = [0.25, 0.5, 0.75, 1.0]
BASE_WS = [0.30, 0.45, 0.60]
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                   "risk3_sweep.csv")


def run(args):
    wstart, th, f, bw, close_df, open_df = args
    sim = rotation_sim(close_df, open_df, base_w=bw, base_etf=BASE_ETF,
                       commission=COMMISSION, start=wstart,
                       tp_half=th, tp_frac=f)
    sv = pd.Series(sim["navs"], index=sim["dates"])
    m = calc_metrics(sv)
    nh = sum(1 for t in sim["trades"] if t["action"] == "tp_half")
    return {"window": wstart, "tp_half": th, "tp_frac": f, "base_w": bw,
            "ann": m["annual_return"], "sharpe": m["sharpe"],
            "dd": m["max_drawdown"], "total": m["total_return"],
            "trades": len(sim["trades"]), "nh": nh}


def main():
    etfs, opens, _ = load_data()
    close_df = pd.DataFrame(etfs).sort_index().ffill()
    open_df = pd.DataFrame(opens).sort_index().reindex(close_df.index).ffill()

    combos = [(wstart, th, f, bw) for wstart in (w for _, w in WINDOWS)
              for th in TP_HALFS for f in TP_FRACS for bw in BASE_WS]
    args = [(ws, th, f, bw, close_df, open_df) for ws, th, f, bw in combos]
    rows = []
    with cf.ProcessPoolExecutor(max_workers=12) as ex:
        for r in ex.map(run, args):
            rows.append(r)
    df = pd.DataFrame(rows)
    df.to_csv(OUT, index=False)
    print(f"saved {OUT} ({len(df)} rows)")


if __name__ == "__main__":
    main()
