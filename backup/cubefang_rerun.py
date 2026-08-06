#!/usr/bin/env python3
"""次方量化"年化上百夏普破2"策略按本仓库口径复刻 (2026-08-06)。

OCR 策略: 15日斜率动量 [0.085,2.55] + 最小持有1天 + 跌幅止损3% + 高点回撤止损4%
+ 止损冷却3天, 买入1只, 池 = 黄金518880/纳指513100/纳指科技159509/嘉实原油160723/
豆粕159985/红利低波512890。

口径对照:
- 次方口径近似: 当日信号当日收盘成交, 佣金万0.5, 0滑点 (14:50 决策=当日执行)
- 我方口径 (v7): 昨日收盘信号->今日开盘成交, 佣金万3, 0滑点, 无跌停豁免
- 变体: 我方口径+0.2%滑点; 2020起短窗
"""
import json

import numpy as np
import pandas as pd

from backtest import calc_metrics

POOL = {"518880": "黄金", "513100": "纳指", "159509": "纳指科技",
        "160723": "嘉实原油", "159985": "豆粕", "512890": "红利低波"}
START = "2015-01-01"
SLOPE_LO, SLOPE_HI = 0.085, 2.55
STOP_LOSS, STOP_TRAIL, COOL = 0.03, 0.04, 3


def load():
    etfs, opens = {}, {}
    for code in POOL:
        d = json.load(open(f"cache_bt/etf_industry/{code}.json"))
        idx = pd.to_datetime(d["dates"])
        etfs[code] = pd.Series(d["close"], index=idx).sort_index()
        opens[code] = pd.Series(d["open"], index=idx).sort_index()
    return pd.DataFrame(etfs).ffill(), pd.DataFrame(opens).ffill()


def run(close, open_, *, same_day=False, comm=0.0003, slippage=0.0, start=START,
        limit_up_down=False):
    close = close.loc[close.index >= pd.Timestamp(start)]
    open_ = open_.reindex(close.index).ffill()
    sig = close if same_day else close.shift(1)   # 信号价
    mom = sig.pct_change(15)
    fill = open_.fillna(close) if not open_.isna().all().all() else close

    cash = 1.0
    shares = 0.0
    code, cost, peak, last_sell = None, 0.0, 0.0, {}
    trades, dates, navs = [], [], []

    for i, date in enumerate(close.index):
        day_trades = []
        if code is not None:
            px = sig.loc[date, code]
            if not np.isnan(px):
                peak = max(peak, px)
                dret = close.loc[date, code] / sig.loc[date, code] - 1 if not np.isnan(close.loc[date, code]) else 0
                if px <= cost * (1 - STOP_LOSS):
                    reason = "loss"
                elif px <= peak * (1 - STOP_TRAIL):
                    reason = "trail"
                else:
                    reason = None
                if reason and limit_up_down and dret <= -0.095:
                    reason = None
                if reason:
                    ex = fill.loc[date, code] * (1 - slippage)
                    cash += shares * ex * (1 - comm)
                    last_sell[code] = i
                    trades.append({"date": date, "action": "sell", "code": code,
                                   "reason": reason})
                    code, cost, peak, shares = None, 0.0, 0.0, 0.0

        elig = [c for c in close.columns
                if not np.isnan(mom.loc[date, c])
                and SLOPE_LO <= mom.loc[date, c] <= SLOPE_HI
                and i - last_sell.get(c, -10**9) > COOL]
        if elig:
            best = max(elig, key=lambda c: mom.loc[date, c])
            if best != code:
                if code is not None:
                    ex = fill.loc[date, code] * (1 - slippage)
                    cash += shares * ex * (1 - comm)
                    trades.append({"date": date, "action": "sell", "code": code,
                                   "reason": "switch"})
                bp = fill.loc[date, best] * (1 + slippage)
                shares = cash / bp
                cash = 0.0
                code, cost, peak = best, bp, bp
                trades.append({"date": date, "action": "buy", "code": best,
                               "reason": "new"})

        px = close.loc[date, code] if code else np.nan
        navs.append(cash + (shares * px if code else 0))
        dates.append(date)

    sv = pd.Series(navs, index=dates)
    m = calc_metrics(sv)
    n_trades = len(trades)
    n_buys = sum(1 for t in trades if t["action"] == "buy")
    return sv, m, trades, n_trades, n_buys


def yearly(sv):
    yr = sv.resample("YE").last()
    ret = yr.pct_change() * 100
    ret.iloc[0] = (yr.iloc[0] / sv.iloc[0] - 1) * 100
    return ret.round(1).astype(str).map(lambda s: s + "%")


def main():
    close, open_ = load()
    variants = [
        ("次方口径近似 (同日收盘/万0.5/0滑点)", dict(same_day=True, comm=0.0005)),
        ("我方口径 (昨信号今开盘/万3/0滑点)", dict(same_day=False, comm=0.0003)),
        ("我方口径 + 0.2%滑点", dict(same_day=False, comm=0.0003, slippage=0.002)),
        ("我方口径 2020起短窗", dict(same_day=False, comm=0.0003, start="2020-01-01")),
        ("我方口径 2021-06起(池全可用)", dict(same_day=False, comm=0.0003, start="2021-06-24")),
        ("次方口径 2021-06起(池全可用)", dict(same_day=True, comm=0.0005, start="2021-06-24")),
        ("次方口径+跌停豁免 2021-06起", dict(same_day=True, comm=0.0005, start="2021-06-24",
                                             limit_up_down=True)),
    ]
    print(f"{'变体':<36} {'年化':>7} {'夏普':>6} {'回撤':>7} {'总收益':>9} {'买卖次数':>7} {'最长持仓':>7}")
    for name, kw in variants:
        sv, m, trades, n, nb = run(close, open_, **kw)
        days = 0
        cur = None
        s = 0
        maxd = 0
        for t in trades:
            if t["action"] == "buy":
                cur, s = t["date"], 0
            else:
                if cur is not None:
                    s = (pd.Timestamp(t["date"]) - pd.Timestamp(cur)).days
                    maxd = max(maxd, s)
        print(f"{name:<36} {m['annual_return']:6.1f}% {m['sharpe']:6.2f} "
              f"{m['max_drawdown']:6.1f}% {m['total_return']:8.1f}% {n:6d} {maxd:6d}d")

    print("\n年度收益 (我方口径):")
    sv, m, trades, _, _ = run(close, open_, same_day=False, comm=0.0003)
    print(yearly(sv).to_string())
    print("\n我方口径持仓分段 (前30段):")
    holds, cur = [], None
    for t in trades:
        if t["action"] == "buy":
            cur = t["date"]
        elif cur is not None:
            holds.append((t["code"], cur, t["date"]))
            cur = None
    if cur is not None:
        holds.append((t["code"], cur, close.index[-1]))
    for c, s, e in holds[:30]:
        print(f"  {POOL[c]:<8} {s} ~ {e}  ({(pd.Timestamp(e)-pd.Timestamp(s)).days}天)")


if __name__ == "__main__":
    main()
