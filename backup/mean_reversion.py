#!/usr/bin/env python3
"""均值回归实验 (v8 候选): 横截面"买最惨"轮动。

规则 (与 v7 同成交模型, 防前视):
- 每 REBAL 个交易日调仓: 用昨日收盘信号, 今日开盘价成交
- 在 26 只 ETF 池中选过去 LOOK 日收益最低的 TOP_K 只, 等权买入
- 无候选(全 NaN)时空仓吃现金 CASH_APR
- 佣金 COMMISSION (万3, 同 v7)
"""

import glob
import json
import os
from datetime import datetime

import numpy as np
import pandas as pd

from backtest import COMMISSION, calc_metrics
from rot_core import CASH_APR, ETFS_DIR

BT_START = "2015-01-01"


def load_data():
    etfs, opens = {}, {}
    for f in sorted(glob.glob(os.path.join(ETFS_DIR, "*.json"))):
        d = json.load(open(f))
        dates = pd.to_datetime(d["dates"])
        etfs[d["code"]] = pd.Series(d["close"], index=dates).sort_index()
        opens[d["code"]] = pd.Series(d["open"], index=dates).sort_index()
    return etfs, opens


def mr_sim(close_df, open_df, *, look, rebal, top_k, start=BT_START):
    """横截面均值回归: 每 rebal 天买过去 look 日收益最低的 top_k 只等权。"""
    close_df = close_df.loc[close_df.index >= pd.Timestamp(start)]
    open_df = open_df.loc[open_df.index >= pd.Timestamp(start)]
    sig = close_df.shift(1)                    # 昨日收盘信号
    ret = sig.pct_change(look)                 # look 日收益 (昨日收盘口径)
    fill = open_df
    n = len(close_df)
    n_hold = 0
    cash = 1.0
    shares = {}
    navs, dates = [], []
    trades = []

    for i, date in enumerate(close_df.index):
        if n_hold == 0 and i >= look:
            r = ret.iloc[i].dropna()
            if len(r) >= top_k:
                picks = r.nsmallest(top_k).index
                px = fill.loc[date, picks]
                w = 1.0 / top_k
                for c in picks:
                    shares[c] = (cash * w) / px[c]
                    cash -= cash * w + cash * w * COMMISSION
                trades.append({"date": date, "action": "buy",
                               "codes": "+".join(picks)})
                n_hold = rebal
        elif n_hold > 0:
            n_hold -= 1
            if n_hold == 0:
                for c, s in shares.items():
                    px = fill.loc[date, c]
                    cash += s * px * (1 - COMMISSION)
                trades.append({"date": date, "action": "sell"})
                shares = {}

        mv = cash + sum(s * close_df.loc[date, c] for c, s in shares.items())
        navs.append(mv)
        dates.append(date)

    return pd.Series(navs, index=dates), trades


def main():
    etfs, opens = load_data()
    close_df = pd.DataFrame(etfs).sort_index().ffill()
    open_df = pd.DataFrame(opens).sort_index().reindex(close_df.index).ffill()

    rows = []
    for look in (5, 10, 20, 60):
        for rebal in (5, 10, 20):
            for top_k in (1, 2, 3):
                sv, trades = mr_sim(close_df, open_df, look=look, rebal=rebal, top_k=top_k)
                sm = calc_metrics(sv)
                n_tr = len(trades)
                rows.append({"look": look, "rebal": rebal, "top_k": top_k,
                             "年化%": round(sm["annual_return"], 1),
                             "夏普": round(sm["sharpe"], 2),
                             "回撤%": round(sm["max_drawdown"], 1),
                             "总收益%": round(sm["total_return"], 1),
                             "交易次数": n_tr})
    res = pd.DataFrame(rows).sort_values("夏普", ascending=False)
    print(res.to_string(index=False))
    res.to_csv(f"mean_rev_sweep_{datetime.now():%Y%m%d_%H%M%S}.csv", index=False)

    best = res.iloc[0]
    print(f"\n最佳: look={int(best.look)} rebal={int(best.rebal)} top_k={int(best.top_k)}")
    print(f"     年化{best['年化%']}% 夏普{best['夏普']} 回撤{best['回撤%']}%")
    sv, trades = mr_sim(close_df, open_df, look=int(best.look), rebal=int(best.rebal),
                        top_k=int(best.top_k))
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    pd.DataFrame({"date": sv.index, "nav": sv.values}).to_csv(
        f"mean_rev_result_{ts}.csv", index=False, encoding="utf-8-sig")
    if trades:
        pd.DataFrame(trades).to_csv(f"mean_rev_trades_{ts}.csv", index=False)
    print(f"      CSV -> mean_rev_result_{ts}.csv")


if __name__ == "__main__":
    main()
