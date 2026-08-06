#!/usr/bin/env python3
"""v7 + 部分止盈实验 (2026-08-06): 轮动仓浮盈达 tp_half 卖一半落袋, 剩余跟 trail。

来源: "吃掉主升浪后在大回撤之前离场" 的机制探索 — 半止盈不改收益期望,
只把尾部回撤痛苦减半 (预期收益不变/略降, 但回撤应下降)。
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd

from backtest import COMMISSION, calc_metrics
from etf_rot_signal import load_data, load_benches
from rot_core import rotation_sim, BASE_W, BASE_ETF

WINDOWS = [("2015起", "2015-01-01"), ("2020起", "2020-01-01")]
TPS = [0.3, 0.5, 0.7, 1.0, None]


def run(close_df, open_df, start, tp_half):
    sim = rotation_sim(close_df, open_df, base_w=BASE_W, base_etf=BASE_ETF,
                       commission=COMMISSION, start=start, tp_half=tp_half)
    sv = pd.Series(sim["navs"], index=sim["dates"])
    return calc_metrics(sv), sim["trades"]


def main():
    etfs, opens, _ = load_data()
    close_df = pd.DataFrame(etfs).sort_index().ffill()
    open_df = pd.DataFrame(opens).sort_index().reindex(close_df.index).ffill()
    nd = pd.Series(load_benches()["纳指"], index=pd.to_datetime(load_benches()["纳指"].index))

    for wname, wstart in WINDOWS:
        base, _ = run(close_df, open_df, wstart, None)
        win = nd[nd.index >= pd.Timestamp(wstart)]
        nd_ann = (win.iloc[-1] / win.iloc[0]) ** (252 / len(win)) - 1
        print(f"\n== {wname} (基线: {base['annual_return']:.1f}%/夏普{base['sharpe']:.2f}/"
              f"回撤{base['max_drawdown']:.1f}%, 纳指同期 {nd_ann*100:.1f}%) ==")
        print(f"{'tp_half':>8} {'年化':>7} {'夏普':>6} {'回撤':>7} {'总收益':>9} {'交易数':>6} "
              f"{'半止盈次':>7} {'vs基线':>7}")
        for tp in TPS:
            m, trades = run(close_df, open_df, wstart, tp)
            nh = sum(1 for t in trades if t["action"] == "tp_half")
            print(f"{str(tp):>8} {m['annual_return']:6.1f}% {m['sharpe']:6.2f} "
                  f"{m['max_drawdown']:6.1f}% {m['total_return']:8.1f}% "
                  f"{len(trades):6d} {nh:7d} {m['annual_return']-base['annual_return']:+6.1f}pp")


if __name__ == "__main__":
    main()
