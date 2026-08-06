#!/usr/bin/env python3
"""次方量化 RSRS 策略按本仓库口径复刻 (2026-08-06)。

OCR: RSRS 动量(27日 high/low 回归斜率 z-score, 阈值 0~3.7) + 最小持有2天
+ 收益止盈14%(冷却5天) + 跌幅止损6%(冷却2天), 买入1只,
池 = 纳指159941/黄金518880/创业板100/159915/豆粕159985。佣金万1.1, 滑点0.1%。

口径对照: 次方口径(当日信号当日收盘成交) vs 我方口径(昨日信号今日开盘, 万3)。
"""
import json

import numpy as np
import pandas as pd

from backtest import calc_metrics

POOL = {"159941": "纳指100", "518880": "黄金", "159915": "创业板100", "159985": "豆粕"}
START = "2015-01-01"
RSRS_N, RSRS_Z, RSRS_LO, RSRS_HI = 27, 200, 0.0, 3.7
TP, TP_COOL = 0.14, 5
SL, SL_COOL = 0.06, 2
MIN_HOLD = 2
DIR = "cache_bt/rsrs_test"


def load():
    etfs, opens = {}, {}
    for code in POOL:
        d = json.load(open(f"{DIR}/{code}.json"))
        idx = pd.to_datetime(d["dates"])
        etfs[code] = pd.Series(d["close"], index=idx).sort_index()
        opens[code] = pd.Series(d["open"], index=idx).sort_index()
    return pd.DataFrame(etfs).ffill(), pd.DataFrame(opens).ffill()


def rsrs_zscore(high, low, n=RSRS_N, z=RSRS_Z):
    hm, lm = high.rolling(n).mean(), low.rolling(n).mean()
    cov = (high * low).rolling(n).mean() - hm * lm
    var = (low ** 2).rolling(n).mean() - lm ** 2
    beta = cov / var
    return (beta - beta.rolling(z).mean()) / beta.rolling(z).std()


def wslope_z(close, n=26, z=200):
    """加权线性回归斜率 (近端权重大) / 价格, 滚动 z-score。"""
    w = np.arange(1, n + 1, dtype=float)
    x = np.arange(n, dtype=float)
    xm = (w * x).sum() / w.sum()
    den = (w * (x - xm) ** 2).sum()

    def wls(a):
        wm = (w * a).sum() / w.sum()
        return (w * (x - xm) * (a - wm)).sum() / den

    b = close.rolling(n).apply(wls, raw=True) / close
    return (b - b.rolling(z).mean()) / b.rolling(z).std()


def run(close, open_, high, low, *, same_day=False, comm=0.0003, slippage=0.0,
        start=START, momentum="rsrs"):
    close = close.loc[close.index >= pd.Timestamp(start)]
    open_ = open_.reindex(close.index).ffill()
    high = high.reindex(close.index).ffill()
    low = low.reindex(close.index).ffill()
    sig = close if same_day else close.shift(1)
    fill = open_.fillna(close) if not open_.isna().all().all() else close
    if momentum == "rsrs":
        rsrs = rsrs_zscore(high if same_day else high.shift(1),
                           low if same_day else low.shift(1))
    else:
        rsrs = wslope_z(sig)

    cash, shares = 1.0, 0.0
    code, cost, buy_i, last_tp, last_sl = None, 0.0, 0, {}, {}
    trades, dates, navs = [], [], []

    for i, date in enumerate(close.index):
        day_trades = []
        if code is not None and i - buy_i >= MIN_HOLD:
            px = sig.loc[date, code]
            if not np.isnan(px):
                if px >= cost * (1 + TP):
                    reason = "tp"
                    last_tp[code] = i
                elif px <= cost * (1 - SL):
                    reason = "sl"
                    last_sl[code] = i
                else:
                    reason = None
                if reason:
                    ex = fill.loc[date, code] * (1 - slippage)
                    cash += shares * ex * (1 - comm)
                    trades.append({"date": date, "action": "sell", "code": code,
                                   "reason": reason})
                    code, cost, shares = None, 0.0, 0.0

        elig = [c for c in close.columns
                if not np.isnan(rsrs.loc[date, c])
                and RSRS_LO <= rsrs.loc[date, c] <= RSRS_HI
                and i - last_tp.get(c, -10**9) > TP_COOL
                and i - last_sl.get(c, -10**9) > SL_COOL]
        if elig:
            best = max(elig, key=lambda c: rsrs.loc[date, c])
            if best != code:
                if code is not None:
                    ex = fill.loc[date, code] * (1 - slippage)
                    cash += shares * ex * (1 - comm)
                    trades.append({"date": date, "action": "sell", "code": code,
                                   "reason": "switch"})
                bp = fill.loc[date, best] * (1 + slippage)
                shares, cash = cash / bp, 0.0
                code, cost, buy_i = best, bp, i
                trades.append({"date": date, "action": "buy", "code": best,
                               "reason": "new"})

        px = close.loc[date, code] if code else np.nan
        navs.append(cash + (shares * px if code else 0))
        dates.append(date)

    sv = pd.Series(navs, index=dates)
    m = calc_metrics(sv)
    return sv, m, len(trades)


def main():
    close, open_ = load()
    idx = close.index
    high = close.copy()
    low = close.copy()
    for code in POOL:
        d = json.load(open(f"{DIR}/{code}.json"))
        i2 = pd.to_datetime(d["dates"])
        high[code] = pd.Series(d["high"], index=i2).sort_index()
        low[code] = pd.Series(d["low"], index=i2).sort_index()
    high, low = high.ffill(), low.ffill()

    variants = [
        ("次方口径 (同日收盘/万1.1/滑0.1%)", dict(same_day=True, comm=0.00011, slippage=0.001)),
        ("次方口径 2020-01起(池全可用)", dict(same_day=True, comm=0.00011, slippage=0.001,
                                          start="2020-01-01")),
        ("我方口径 (昨信号今开盘/万3)", dict(same_day=False, comm=0.0003)),
        ("我方口径 2020-01起", dict(same_day=False, comm=0.0003, start="2020-01-01")),
        ("我方口径 +0.2%滑点", dict(same_day=False, comm=0.0003, slippage=0.002)),
        ("加权斜率版 次方口径", dict(same_day=True, comm=0.00011, slippage=0.001,
                                momentum="wslope")),
        ("加权斜率版 我方口径", dict(same_day=False, comm=0.0003, momentum="wslope")),
        ("加权斜率版 我方+0.2%滑点", dict(same_day=False, comm=0.0003, slippage=0.002,
                                      momentum="wslope")),
    ]
    print(f"{'变体':<34} {'年化':>7} {'夏普':>6} {'回撤':>7} {'总收益':>9} {'交易数':>6}")
    for name, kw in variants:
        sv, m, n = run(close, open_, high, low, **kw)
        print(f"{name:<34} {m['annual_return']:6.1f}% {m['sharpe']:6.2f} "
              f"{m['max_drawdown']:6.1f}% {m['total_return']:8.1f}% {n:6d}")

    print("\n年度收益 (我方口径):")
    sv, _, _ = run(close, open_, high, low, same_day=False, comm=0.0003)
    yr = sv.resample("YE").last()
    ret = yr.pct_change() * 100
    ret.iloc[0] = (yr.iloc[0] / sv.iloc[0] - 1) * 100
    print(ret.round(1).astype(str).map(lambda s: s + "%").to_string())


if __name__ == "__main__":
    main()
