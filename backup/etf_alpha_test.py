#!/usr/bin/env python3
"""alpha191 横截面打分选轮动标的实验 (23只精选池, 替换 180日动量信号)。

从 alpha191 (国泰君安短周期价量因子, 基于 WorldQuant 101) 中挑 close/open 可实现的
时间序列类因子, 每日对池内 ETF 打分, 选分数最高者持有 (其余逻辑: MA200门控/止损/
冷却/底仓45%黄金 与 v7 完全一致)。缓存无 high/low/volume, 先用价量无关因子,
有效果再拉全量数据。实验脚本不写 rot_core (防漂移), 复制 rotation_sim 改打分。
"""

import sys
import os
from datetime import datetime

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from backtest import COMMISSION, calc_metrics
from rot_core import (MA_N, LOOKBACK, MOM_GAP, MIN_MOM, TRAIL, COOLDOWN, BASE_W,
                      BASE_ETF, BT_START)
from etf_rot_signal import load_data, load_benches

ETFS_DIR = "cache_bt/etf_industry"


def make_scores(close_df):
    """返回 {name: (Series<分数>, min_score, gap)} 横截面打分因子集。"""
    ret = close_df.pct_change()
    out = {}
    out["mom180"] = (close_df.pct_change(LOOKBACK), MIN_MOM, MOM_GAP)              # 基线: 180日动量
    out["alpha88"] = (close_df / close_df.shift(20) - 1, 0.0, 0.10)               # 20日动量
    out["alpha53"] = (close_df.gt(close_df.shift()).rolling(12).sum() / 12, 0.5, 0.1)  # 12日上涨占比
    out["alpha58"] = (close_df.gt(close_df.shift()).rolling(20).sum() / 20, 0.5, 0.1)  # 20日上涨占比
    out["alpha31"] = ((close_df - close_df.rolling(12).mean()) / close_df.rolling(12).mean(), 0.0, 0.02)  # 12日均线偏离
    out["vol_mom"] = (close_df.pct_change(LOOKBACK) / ret.rolling(60).std(), 0.0, 0.5)  # 波动率调整动量
    combo = (out["mom180"][0].rank(axis=1) + out["alpha88"][0].rank(axis=1) + out["alpha58"][0].rank(axis=1))
    out["combo3"] = (combo, 0.0, 3.0)                                            # 三因子 rank 和
    return out


def score_sim(close_df, open_df, score, min_score, gap, *,
              ma_n=MA_N, trail=TRAIL, cooldown=COOLDOWN, base_w=BASE_W,
              base_etf=BASE_ETF, commission=COMMISSION, start=BT_START):
    """rotation_sim 的复制, 动量信号换成横截面分数 (score 列名须含于 close_df)。"""
    close_df = close_df.loc[close_df.index >= pd.Timestamp(start)]
    open_df = open_df.reindex(close_df.index)
    sig = close_df.shift(1)
    ma = sig.rolling(ma_n).mean()
    above = sig > ma
    peak60 = sig.rolling(61, min_periods=1).max()
    sc = score.shift(1)
    lowvol = close_df[base_etf].fillna(0.0)
    first_px = lowvol[lowvol > 0].iloc[0]
    lowvol = lowvol.replace(0.0, first_px)
    fill = open_df
    base_shares = base_w / lowvol.iloc[0]

    rot_cash = 1.0 - base_w
    shares = 0.0
    code = None
    peak = 0.0
    last_sell = {}
    dates, navs = [], []
    for i, date in enumerate(close_df.index):
        px = sig.loc[date, code] if code else None
        signal = above.loc[date]
        if code is not None:
            peak = max(peak, px)
            if not bool(signal[code]):
                exec_px = fill.loc[date, code]
                rot_cash += shares * exec_px * (1 - commission)
                last_sell[code] = i
                shares, code, peak = 0.0, None, 0.0
            elif px <= peak * (1 - trail):
                exec_px = fill.loc[date, code]
                rot_cash += shares * exec_px * (1 - commission)
                last_sell[code] = i
                shares, code, peak = 0.0, None, 0.0
        srow = sc.loc[date]
        elig = [c for c in signal[signal].index
                if not np.isnan(srow[c]) and srow[c] > min_score
                and (cooldown == 0 or i - last_sell.get(c, -10**9) > cooldown)]
        if elig:
            elig = [c for c in elig if sig.loc[date, c] >= peak60.loc[date, c] * (1 - trail)]
        if len(elig) > 0:
            best = srow.loc[elig].idxmax()
            best_px = fill.loc[date, best]
            if code is None:
                target = rot_cash
                shares = target / best_px
                rot_cash -= target + target * commission
                code = best
                peak = best_px
            elif best != code and srow[best] - srow[code] > gap:
                exec_px = fill.loc[date, code]
                rot_cash += shares * exec_px * (1 - commission)
                shares = 0.0
                target = rot_cash
                shares = target / best_px
                rot_cash -= target + target * commission
                code = best
                peak = best_px
        px = close_df.loc[date, code] if code else None
        navs.append(base_shares * lowvol.iloc[i] + rot_cash + (shares * px if code else 0))
        dates.append(date)
    return pd.Series(navs, index=dates)


def main():
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    print("[1/2] 读取缓存...")
    etfs, opens, _ = load_data()
    close_df = pd.DataFrame(etfs).sort_index().ffill()
    open_df = pd.DataFrame(opens).sort_index().reindex(close_df.index).ffill()
    print(f"      {len(etfs)} 只, {close_df.index[0].date()} ~ {close_df.index[-1].date()}")

    print("[2/2] 横截面打分回测...")
    scores = make_scores(close_df)
    rows = []
    for name, (score, min_score, gap) in scores.items():
        sv = score_sim(close_df, open_df, score, min_score, gap)
        sm = calc_metrics(sv)
        rows.append((name, sm["annual_return"], sm["sharpe"], sm["max_drawdown"], sm["total_return"]))
        print(f"  {name:8s} 年化{sm['annual_return']:6.1f}% 夏普{sm['sharpe']:.2f} 回撤{sm['max_drawdown']:5.1f}% 总收益{sm['total_return']:6.0f}%")
    b = pd.read_pickle  # noqa: 占位防误用
    print("\n对比: 基线 mom180 应为 23.2%/0.98/24.7%/934% (与 etf_rot_signal 一致)")


if __name__ == "__main__":
    main()
