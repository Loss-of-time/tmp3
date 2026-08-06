#!/usr/bin/env python3
"""OCR 策略复刻 (2026-08-06): 19日区间涨幅动量带 0.02~0.1 + 最小持有5天
+ 无止盈止损, 买入1只, 池 = 华宝油气LOF(162411)/纳指ETF(513100)/
标普信息科技LOF(161128)/科创50ETF(588000)/美国消费LOF(162415)/红利低波50ETF(515450)。
佣金万2.5, 滑点0.1%。口径: 平台口径(14:50信号当日收盘成交) vs 我方口径(昨信号今开盘)。
"""
import json
import os
import sys
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import pandas as pd

from backtest import calc_metrics
import fetch_etf_industry as f

POOL = {"162411": "华宝油气", "513100": "纳指", "161128": "标普信息科技",
        "588000": "科创50", "162415": "美国消费", "515450": "红利低波50"}
MOM_N, LO, HI, MIN_HOLD = 19, 0.02, 0.1, 5
DIR = "cache_bt/zf_mom19"


def fetch(code, name):
    fh = os.path.join(DIR, f"{code}.json")
    if os.path.exists(fh):
        return "cached"
    pref = ("sz" if code.startswith("1") else "sh") + code
    rows, end = [], f.END_TX
    for attempt in range(12):
        try:
            while True:
                url = f"{f.TENCENT_URL}?param={pref},day,,{end},640,qfq"
                req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
                d = json.load(urllib.request.urlopen(req, timeout=15))
                day = d["data"][pref].get("day") or d["data"][pref].get("qfqday")
                page = [(r[0], float(r[1]), float(r[2])) for r in day]
                rows = page + rows
                if len(page) < 640:
                    break
                end = page[0][0]
            seen, dedup = set(), []
            for r in rows:
                if r[0] not in seen:
                    seen.add(r[0])
                    dedup.append(r)
            with open(fh, "w") as fh2:
                json.dump({"code": code, "name": name,
                           "dates": [r[0] for r in dedup],
                           "open": [r[1] for r in dedup],
                           "close": [r[2] for r in dedup]}, fh2)
            return f"{len(dedup)} 行"
        except Exception as e:
            if attempt == 11:
                return f"fail: {e}"
            import time
            time.sleep(15)


def load():
    close, open_ = {}, {}
    for code in POOL:
        d = json.load(open(f"{DIR}/{code}.json"))
        idx = pd.to_datetime(d["dates"])
        close[code] = pd.Series(d["close"], index=idx).sort_index()
        open_[code] = pd.Series(d["open"], index=idx).sort_index()
    return pd.DataFrame(close).ffill(), pd.DataFrame(open_).ffill()


def run(close, open_, *, same_day=False, comm=0.00025, slippage=0.001,
        start="2020-01-01", mom_n=MOM_N, sell_to_cash=False, hi=HI):
    close = close.loc[close.index >= pd.Timestamp(start)]
    open_ = open_.reindex(close.index).ffill()
    sig = close if same_day else close.shift(1)
    fill = close if same_day else open_.fillna(close)

    mom = sig / sig.shift(mom_n) - 1

    cash, shares = 1.0, 0.0
    code, cost, buy_i = None, 0.0, 0
    trades, dates, navs = [], [], []

    def elig(date):
        return [c for c in close.columns
                if not pd.isna(mom.loc[date, c]) and LO <= mom.loc[date, c] <= (hi or np.inf)]

    for i, date in enumerate(close.index):
        if code is not None and i - buy_i >= MIN_HOLD:
            elig_list = elig(date)
            if sell_to_cash and not elig_list:
                ex = fill.loc[date, code] * (1 - slippage)
                cash += shares * ex * (1 - comm)
                trades.append({"date": date, "action": "sell", "code": code, "reason": "to_cash"})
                code, cost, shares = None, 0.0, 0.0
            elif elig_list:
                best = max(elig_list, key=lambda c: mom.loc[date, c])
                if best != code:
                    if code is not None:
                        ex = fill.loc[date, code] * (1 - slippage)
                        cash += shares * ex * (1 - comm)
                        trades.append({"date": date, "action": "sell", "code": code,
                                       "reason": "switch"})
                    bp = fill.loc[date, best] * (1 + slippage)
                    shares, cash = cash / bp, 0.0
                    code, cost, buy_i = best, bp, i
                    trades.append({"date": date, "action": "buy", "code": best, "reason": "new"})
        elif code is None:
            elig_list = elig(date)
            if elig_list:
                best = max(elig_list, key=lambda c: mom.loc[date, c])
                bp = fill.loc[date, best] * (1 + slippage)
                shares, cash = cash / bp, 0.0
                code, cost, buy_i = best, bp, i
                trades.append({"date": date, "action": "buy", "code": best, "reason": "new"})

        px = close.loc[date, code] if code else 1.0
        navs.append(cash + shares * px)
        dates.append(date)

    sv = pd.Series(navs, index=dates)
    return sv, calc_metrics(sv), trades


def main():
    os.makedirs(DIR, exist_ok=True)
    for code, name in POOL.items():
        print(code, name, fetch(code, name))
    close, open_ = load()
    print(f"\n数据: {close.index.min().date()} ~ {close.index.max().date()}, "
          f"各ETF首日: {close.apply(lambda s: s.first_valid_index().date()).to_dict()}")
    for code in POOL:
        i = list(close.columns).index(code)
        print(f"  {code} 最后一期动量: {(close.iloc[-1, i] / close.iloc[-1-MOM_N, i] - 1):.3f}")

    variants = [
        ("平台口径 (同日收盘/万2.5/滑0.1%)", dict(same_day=True)),
        ("平台口径 18日动量", dict(same_day=True, mom_n=18)),
        ("平台口径 全窗(2016起)", dict(same_day=True, start="2016-01-01")),
        ("平台口径 无动量带(仅>=0.02)", dict(same_day=True, hi=None)),
        ("平台口径 空仓版(带外持现金)", dict(same_day=True, sell_to_cash=True)),
        ("我方口径 (昨信号今开盘/万2.5)", dict(same_day=False)),
        ("我方口径 +0.2%滑点", dict(same_day=False, slippage=0.003)),
    ]
    print(f"\n{'变体':<30} {'年化':>7} {'夏普':>6} {'回撤':>7} {'总收益':>9} {'交易数':>6}")
    for name, kw in variants:
        sv, m, trades = run(close, open_, **kw)
        print(f"{name:<30} {m['annual_return']:6.1f}% {m['sharpe']:6.2f} "
              f"{m['max_drawdown']:6.1f}% {m['total_return']:8.1f}% {len(trades):6d}")

    print("\n年度收益 (平台口径):")
    sv, _, trades = run(close, open_)
    yr = sv.resample("YE").last()
    ret = yr.pct_change() * 100
    ret.iloc[0] = (yr.iloc[0] / sv.iloc[0] - 1) * 100
    print(ret.round(1).astype(str).map(lambda s: s + "%").to_string())
    pd.DataFrame(trades).to_csv("backup/zf_mom19_trades.csv", index=False)
    print(f"\n交易明细 -> backup/zf_mom19_trades.csv ({len(trades)}笔)")


if __name__ == "__main__":
    main()
