#!/usr/bin/env python3
"""v7 + 动量上限过滤实验 (2026-08-06): 180日动量 >= mom_cap 的候选不买入。

来源: OCR 动量带策略(19日 0.02~0.1)启示 — "动量带上限=入场侧过热过滤"。
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd

from backtest import COMMISSION, calc_metrics
from etf_rot_signal import load_data, load_benches
from rot_core import rotation_sim, BASE_W, BASE_ETF

WINDOWS = [("2015起", "2015-01-01"), ("2020起", "2020-01-01")]
CAPS = [0.4, 0.5, 0.6, 0.8, 1.0, 1.5, None]


def run(close_df, open_df, start, mom_cap):
    sim = rotation_sim(close_df, open_df, base_w=BASE_W, base_etf=BASE_ETF,
                       commission=COMMISSION, start=start, mom_cap=mom_cap)
    sv = pd.Series(sim["navs"], index=sim["dates"])
    return calc_metrics(sv), len(sim["trades"])


def main():
    etfs, opens, _ = load_data()
    close_df = pd.DataFrame(etfs).sort_index().ffill()
    open_df = pd.DataFrame(opens).sort_index().reindex(close_df.index).ffill()
    benches = load_benches()
    nd = benches["纳指"]
    nd = pd.Series(nd, index=pd.to_datetime(nd.index))

    for wname, wstart in WINDOWS:
        base, n0 = run(close_df, open_df, wstart, None)
        win = nd[nd.index >= pd.Timestamp(wstart)]
        nd_ann = (win.iloc[-1] / win.iloc[0]) ** (252 / len(win)) - 1
        print(f"\n== {wname} (基线 mom_cap=None: {base['annual_return']:.1f}%/"
              f"夏普{base['sharpe']:.2f}/回撤{base['max_drawdown']:.1f}%, {n0}笔, "
              f"纳指同期 {nd_ann*100:.1f}%) ==")
        print(f"{'mom_cap':>8} {'年化':>7} {'夏普':>6} {'回撤':>7} {'总收益':>9} {'交易数':>6} {'vs基线':>7} {'vs纳指':>7}")
        for cap in CAPS:
            m, n = run(close_df, open_df, wstart, cap)
            print(f"{str(cap):>8} {m['annual_return']:6.1f}% {m['sharpe']:6.2f} "
                  f"{m['max_drawdown']:6.1f}% {m['total_return']:8.1f}% {n:6d} "
                  f"{m['annual_return']-base['annual_return']:+6.1f}pp "
                  f"{m['annual_return']-nd_ann*100:+6.1f}pp")


if __name__ == "__main__":
    main()
