#!/usr/bin/env python3
"""实验: 换仓总是选动量第 N 名 (rank=0 动量第一=基线, rank=1 动量第二...)。

对比默认参数下各 rank 的指标。结论见 research/conclusions.md。
"""
import sys
sys.path.insert(0, ".")
import pandas as pd
from backtest import COMMISSION, calc_metrics
from etf_rot_signal import load_data
from rot_core import rotation_sim, BT_START

close_df, open_df, names, tradable = load_data()
print(f"池 {len(close_df.columns)} 只")

for rank in range(0, 4):
    sim = rotation_sim(close_df, open_df, start=BT_START, tradable=tradable,
                       commission=COMMISSION, pick_rank=rank)
    sv = pd.Series(sim["navs"], index=sim["dates"])
    m = calc_metrics(sv)
    n_switch = sum(1 for t in sim["trades"] if t["action"] == "switch")
    n_buy = sum(1 for t in sim["trades"] if t["action"] == "buy")
    n_tp = sum(1 for t in sim["trades"] if t["action"] == "tp_half")
    print(f"rank={rank}: 年化{m['annual_return']:.1f}% 夏普{m['sharpe']:.2f} "
          f"回撤{m['max_drawdown']:.1f}% 总收益{m['total_return']:.1f}% "
          f"买{n_buy} 切{n_switch} 止盈{n_tp}")
