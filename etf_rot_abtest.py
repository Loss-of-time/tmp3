#!/usr/bin/env python3
"""AB 测试: 旧 12 只轮动池 vs 新 18 只轮动池 (加 机器人/黄金/化工/消费/标普消费/电力)。

同一套参数 (MA200/LB180/R40/TRAIL20%/BASE_W45%) 下分别回测, 对比年化/夏普/回撤,
并列出被选中过的板块 (看新板块是否真的贡献了轮动机会)。

用法: source .venv/bin/activate && python3 -u etf_rot_abtest.py
"""

import glob
import json
import os
from datetime import datetime

import pandas as pd

from backtest import calc_metrics
from etf_rotation import BT_START, load_data, rotation_backtest

NEW_CODES = {"562500", "518880", "516020", "159928", "159529", "159611"}


def run_pool(close_df, label):
    values, trades = rotation_backtest(close_df)
    sm = calc_metrics(values)
    n_buy = sum(1 for t in trades if t["action"] == "buy")
    n_switch = sum(1 for t in trades if t["action"] == "switch")
    picked = sorted({t["code"] for t in trades if t["code"]})
    print(f"[{label}] 年化{sm['annual_return']:5.1f}%  夏普{sm['sharpe']:.2f}  "
          f"回撤{sm['max_drawdown']:4.1f}%  总收益{sm['total_return']:5.1f}%  "
          f"买入{n_buy} 切换{n_switch}")
    print(f"        选中过的板块: {picked}")
    return sm


def main():
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    etfs, hs300_s = load_data()
    all_df = pd.DataFrame(etfs).sort_index().ffill()
    old_df = all_df.drop(columns=[c for c in NEW_CODES if c in all_df.columns])

    print("--- AB 测试 (2020-01 ~ 2026-07, 参数一致) ---")
    sm_old = run_pool(old_df, "旧12只轮动池")
    sm_new = run_pool(all_df, "新18只轮动池")

    rows = [
        {"pool": "旧12只轮动池", "annual_return": sm_old["annual_return"],
         "sharpe": sm_old["sharpe"], "max_drawdown": sm_old["max_drawdown"],
         "total_return": sm_old["total_return"]},
        {"pool": "新18只轮动池", "annual_return": sm_new["annual_return"],
         "sharpe": sm_new["sharpe"], "max_drawdown": sm_new["max_drawdown"],
         "total_return": sm_new["total_return"]},
    ]
    df = pd.DataFrame(rows)
    df.to_csv(f"etf_rot_abtest_{timestamp}.csv", index=False, encoding="utf-8-sig")
    print(f"\nCSV -> etf_rot_abtest_{timestamp}.csv")


if __name__ == "__main__":
    main()
