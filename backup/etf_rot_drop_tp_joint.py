#!/usr/bin/env python3
"""止跌(drop) × 止盈(tp_half/tp_frac) 联合调参 (2026-08-07)。

drop 甜点 8~10% × 10~20d (单独扫描结论) × tp_half 0.6/0.8/1.0 × tp_frac 0.5/1.0,
全部叠加 trail=0.20。双窗。输出 CSV。
"""
import os
import sys
import concurrent.futures as cf
from datetime import datetime

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backtest import COMMISSION, calc_metrics
from etf_rot_signal import load_data
from rot_core import rotation_sim, BASE_ETF, BASE_W

WINDOWS = [("官方", "2016-08-11"), ("2020", "2020-01-01")]
DROPS = [(0, 0.0), (10, 0.08), (20, 0.08), (20, 0.10), (10, 0.10)]
TP_HALFS = [0.6, 0.8, 1.0]
TP_FRACS = [0.5, 1.0]
TS = datetime.now().strftime("%Y%m%d_%H%M%S")
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                   f"drop_tp_joint_{TS}.csv")


def run(args):
    wstart, drop_n, drop_x, tp_half, tp_frac, close_df, open_df, tradable = args
    sim = rotation_sim(close_df, open_df, base_w=BASE_W, base_etf=BASE_ETF,
                       commission=COMMISSION, start=wstart,
                       trail=0.20, tp_half=tp_half, tp_frac=tp_frac,
                       drop_n=drop_n, drop_x=drop_x, tradable=tradable)
    sv = pd.Series(sim["navs"], index=sim["dates"])
    m = calc_metrics(sv)
    return {"window": wstart, "drop": f"{drop_n}d{drop_x:.0%}", "tp_half": tp_half,
            "tp_frac": tp_frac, "ann": m["annual_return"], "sharpe": m["sharpe"],
            "dd": m["max_drawdown"], "total": m["total_return"],
            "trades": len(sim["trades"])}


def main():
    print("载入数据...")
    close_df, open_df, _, tradable = load_data()
    args = [(ws, dn, dx, th, tf, close_df, open_df, tradable)
            for _, ws in WINDOWS
            for dn, dx in DROPS
            for th in TP_HALFS
            for tf in TP_FRACS]
    rows = []
    with cf.ProcessPoolExecutor(max_workers=12) as ex:
        for r in ex.map(run, args):
            rows.append(r)
    df = pd.DataFrame(rows)
    df.to_csv(OUT, index=False)
    for _, ws in WINDOWS:
        sub = df[df.window == ws]
        base = sub[(sub["drop"] == "0d0%") & (sub.tp_half == 0.8) & (sub.tp_frac == 1.0)].iloc[0]
        print(f"\n== {ws} 基线(drop关,0.8/1.0): {base.ann:.2f}% / 夏普{base.sharpe:.2f} "
              f"/ 回撤{base.dd:.1f}% / 成交{base.trades} ==")
        print(f"{'drop':<8}{'tp_half':>7}{'tp_frac':>7} {'年化':>7} {'夏普':>6} "
              f"{'回撤':>7} {'总收益':>8} {'成交':>5} {'Δ年化':>6}")
        for (dn, dx), name in [(d, f"{d[0]}d{d[1]:.0%}") for d in DROPS]:
            for th in TP_HALFS:
                for tf in TP_FRACS:
                    r = sub[(sub["drop"] == name) & (sub.tp_half == th) & (sub.tp_frac == tf)].iloc[0]
                    mark = " <-" if name != "0d0%" and r.ann > base.ann else ""
                    print(f"{name:<8}{th:>7.1f}{tf:>7.1f} {r.ann:7.2f} {r.sharpe:6.2f} "
                          f"{r.dd:7.1f} {r.total:8.1f} {r.trades:5d} "
                          f"{(r.ann-base.ann):+6.2f}{mark}")
    print(f"\nsaved {OUT} ({len(df)} rows)")


if __name__ == "__main__":
    main()
