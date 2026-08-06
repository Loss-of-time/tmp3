#!/usr/bin/env python3
"""v5 轮动 + v2 威科夫思想优化实验 (A 入场放量突破过滤 + B 出场量能离场)。

基准 = etf_rotation.rotation_backtest 原版。对比四组:
  1. base   原版
  2. entry  A: 调仓日动量最强候选必须放量突破前20日高点才买/切, 否则维持现状
  3. exit   B: 持仓放量滞涨(vol>=2x vma20 且涨幅<1%)减半, 天量(>=4x)清仓
  4. both   A+B 组合

volume 缓存独立存 cache_bt/etf_industry_vol/, 不污染 close 缓存
(paper_trade.py 增量更新 close 时会重写 JSON, 混存会被丢)。
"""

import glob
import json
import os
import time
import urllib.request

import numpy as np
import pandas as pd

from backtest import COMMISSION, calc_metrics
from etf_rotation import ETFS_DIR, HS300_CACHE, load_data
from fetch_etf_industry import ETFS, NEW_ETFS, LOWVOL

# --- wyckoff-style params (scaled to ETFs) ---
ENTRY_VOL = 1.5      # 入场: 放量倍数 >= x vma20
ENTRY_HI = 20        # 入场: close > 前 N 日最高 (突破)
DIST_VOL = 2.0       # 放量滞涨: vol >= x vma20 且涨幅 < DIST_RET
DIST_RET = 0.01
HUGE_VOL = 4.0       # 天量清仓: vol >= x vma20

VOL_DIR = "cache_bt/etf_industry_vol"
ALL_ETFS = {**ETFS, **NEW_ETFS, **LOWVOL}
END_TX = "2026-07-31"


def fetch_volume(code, name):
    os.makedirs(VOL_DIR, exist_ok=True)
    f = os.path.join(VOL_DIR, f"{code}.json")
    if os.path.exists(f):
        return
    pref = ("sz" if code.startswith("1") else "sh") + code
    rows, end = [], END_TX
    while True:
        url = (f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
               f"?param={pref},day,,{end},640,qfq")
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        d = json.load(urllib.request.urlopen(req, timeout=15))
        day = d["data"][pref].get("day") or d["data"][pref].get("qfqday")
        page = [(r[0], float(r[5])) for r in day]
        rows = page + rows
        if len(page) < 640:
            break
        end = page[0][0]
        time.sleep(0.3)
    seen, dedup = set(), []
    for dt, v in rows:
        if dt not in seen:
            seen.add(dt)
            dedup.append([dt, v])
    with open(f, "w") as fh:
        json.dump({"code": code, "name": name,
                   "dates": [r[0] for r in dedup], "volume": [r[1] for r in dedup]}, fh)
    print(f"  {code} {name}: {len(dedup)} 行 volume")


def load_volumes():
    vols = {}
    for code in ALL_ETFS:
        f = os.path.join(VOL_DIR, f"{code}.json")
        if not os.path.exists(f):
            continue
        d = json.load(open(f))
        vols[code] = pd.Series(d["volume"], index=pd.to_datetime(d["dates"]))
    return vols


def rot_backtest(close_df, vol_df, bt_start, filter_entry, vol_exit,
                 ma_n=200, lookback=180, rebal=40, trail=0.20,
                 base_w=0.45, base_etf="512890"):
    """rotation_backtest 变体: filter_entry=A 入场过滤, vol_exit=B 量能离场。"""
    bt = close_df[bt_start:]
    ma = bt.rolling(ma_n).mean()
    above = bt > ma
    mom = bt.pct_change(lookback)
    lowvol = bt[base_etf]
    vma = vol_df.rolling(20).mean()

    values = pd.Series(index=bt.index, dtype=float)
    base_shares = base_w / lowvol.iloc[0]
    rot_cash = 1.0 - base_w
    shares, code, peak, prev_px, half_sold = 0.0, None, 0.0, None, False
    trades = []

    def px_of(c):
        return close_df.loc[date, c]

    for i, date in enumerate(bt.index):
        if i == 0:
            values.iloc[i] = 1.0
            continue
        px = px_of(code) if code else None
        is_rebal = (i % rebal == 0)
        signal = above.loc[date]

        if code is not None:
            peak = max(peak, px)
            reason = None
            if not bool(signal[code]):
                reason = "trend"
            elif px <= peak * (1 - trail):
                reason = "trail"
            elif vol_exit and prev_px is not None:
                v, vm = vol_df.loc[date, code], vma.loc[date, code]
                if not np.isnan(vm) and vm > 0:
                    if v >= HUGE_VOL * vm:
                        reason = "huge_vol"
                    elif v >= DIST_VOL * vm and (px - prev_px) / prev_px < DIST_RET:
                        if half_sold:
                            reason = "dist"
                        else:
                            half_sold = True
                            proceeds = shares / 2 * px * (1 - COMMISSION)
                            rot_cash += proceeds
                            shares /= 2
                            trades.append({"date": date, "action": "half",
                                           "code": None, "reason": "half_dist"})
            if reason:
                proceeds = shares * px * (1 - COMMISSION)
                rot_cash += proceeds
                shares, code, peak, half_sold = 0.0, None, 0.0, False
                trades.append({"date": date, "action": "sell", "code": None,
                               "reason": reason})

        if is_rebal:
            elig = [c for c in signal[signal].index if not np.isnan(mom.loc[date, c])]
            if len(elig) > 0:
                best = mom.loc[date, elig].idxmax()
                best_px = px_of(best)
                do_it = True
                if filter_entry:
                    v, vm = vol_df.loc[date, best], vma.loc[date, best]
                    hi20 = close_df[best].rolling(ENTRY_HI).max().shift(1)
                    ok = (not np.isnan(vm)) and vm > 0 and v >= ENTRY_VOL * vm \
                        and best_px > hi20.loc[date]
                    if not ok:
                        do_it = False  # 未放量突破: 维持现状 (宁缺毋滥)
                if do_it:
                    if code is None:
                        target = rot_cash
                        shares = target / best_px
                        rot_cash -= target + target * COMMISSION
                        code, peak, prev_px, half_sold = best, best_px, best_px, False
                        trades.append({"date": date, "action": "buy", "code": best,
                                       "reason": "new"})
                    elif best != code:
                        old = code
                        rot_cash += shares * px * (1 - COMMISSION)
                        target = rot_cash
                        shares = target / best_px
                        rot_cash -= target + target * COMMISSION
                        code, peak, prev_px, half_sold = best, best_px, best_px, False
                        trades.append({"date": date, "action": "switch", "code": best,
                                       "reason": f"换 {old}"})
        prev_px = px
        mv = base_shares * lowvol.iloc[i] + rot_cash + (shares * px_of(code) if code else 0)
        values.iloc[i] = mv

    return values, trades


def main():
    print("[1/3] 补拉 volume 缓存...")
    for code, name in ALL_ETFS.items():
        fetch_volume(code, name)
    vols = load_volumes()
    if len(vols) < len(ALL_ETFS):
        print(f"  警告: 只拉到 {len(vols)}/{len(ALL_ETFS)} 只 volume")

    print("[2/3] 读取 close 缓存...")
    etfs, hs300_s = load_data()
    close_df = pd.DataFrame(etfs).sort_index().ffill()
    vol_df = pd.DataFrame(vols).reindex(close_df.index).ffill()
    names = {json.load(open(f))["code"]: json.load(open(f))["name"]
             for f in glob.glob(os.path.join(ETFS_DIR, "*.json"))}

    print("[3/3] 四组回测对比...")
    results = {}
    for label, fe, ve in [("base", False, False), ("entry", True, False),
                          ("exit", False, True), ("both", True, True)]:
        values, trades = rot_backtest(close_df, vol_df, "2020-01-01", fe, ve)
        hs = hs300_s.reindex(values.index).ffill()
        common = values.dropna().index.intersection(hs.dropna().index)
        sv, bv = values.reindex(common).ffill(), hs.reindex(common).ffill()
        sm = calc_metrics(sv)
        results[label] = (sm, sv, trades)
        n_buy = sum(1 for t in trades if t["action"] == "buy")
        n_sw = sum(1 for t in trades if t["action"] == "switch")
        n_half = sum(1 for t in trades if t["action"] == "half")
        print(f"  {label:6s} 年化 {sm['annual_return']:6.1f}%  夏普 {sm['sharpe']:.2f}  "
              f"回撤 {sm['max_drawdown']:5.1f}%  总收益 {sm['total_return']:6.1f}%  "
              f"买入{n_buy} 切换{n_sw} 减半{n_half}")

    print("\n--- 交易明细 (both) ---")
    for t in results["both"][2]:
        code = t["code"]
        nm = names.get(code, "-") if code else "-"
        print(f"  {str(t['date'])[:10]} {t['action']:6s} {nm}  {t['reason']}")


if __name__ == "__main__":
    main()
