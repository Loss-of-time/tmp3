#!/usr/bin/env python3
"""v7 + v4.1 创业板择时混合实验。

v4.1 = 159915 的 MA300 牛熊开关 (信号滞后1日成交, 无底仓, 现金2%), 自带
"多数小亏、少数翻倍" 形态。

两种混合方式:
  A. 入池 (mix): 把 v4.1 NAV 当第 20 只候选进轮动池 —— 实测 0 次被选中
     (宽基 180 日动量拼不过最强板块), 结果与 base 完全一致
  B. 拆仓 (split): 轮动仓拆出 CYB_W 档固定跑 v4.1, 其余仍跑 v7 轮动,
     真正保留 v4.1 形态。总净值 = base 低波底仓 + v4.1 档 + v7 轮动档
  C. 牛熊门控 (gate): 用 v4.1 的 MA300 牛熊开关做全局门控 —— 牛(创业板>MA300)
     满仓创业板(v4.1, 峰值止损), 熊(跌破 MA300)切换去跑 v7 轮动, 切换扣双向费
"""

import glob
import json
import os
from datetime import datetime

import numpy as np
import pandas as pd

from backtest import COMMISSION, calc_metrics
from etf_rot_signal import (BASE_ETF, BASE_W, CASH_APR, ETFS_DIR, load_benches,
                            load_data, plot_results, rotation_backtest)

CYB_CACHE = "cache_bt/etf159915.json"
CYB_MA = 300
CYB_TRAIL = 0.20
CYB_BT_START = "2013-01-01"
CYB_CODE = "159915V4"
CYB_NAME = "创业板择时(v4.1)"
OUTPUT_DIR = "output"   # 统一输出目录


def cyb_timing_nav(close, ma_n=CYB_MA, trail=CYB_TRAIL, bt_start=CYB_BT_START):
    """v4.1 牛熊开关 NAV: 信号用昨日收盘, 成交用今日收盘, 无底仓, 现金 2%。"""
    bt = close[bt_start:]
    sig = bt.shift(1)
    hold = sig > bt.rolling(ma_n).mean()
    values = pd.Series(index=bt.index, dtype=float)
    cash, shares, holding, peak = 1.0, 0.0, False, 0.0
    for i, date in enumerate(bt.index):
        if i == 0:
            values.iloc[i] = 1.0
            continue
        px = bt.loc[date]
        signal = hold.loc[date]
        if np.isnan(signal):
            signal = holding
        if holding:
            peak = max(peak, px)
        if trail > 0 and holding and px <= peak * (1 - trail):
            signal = False
        if not holding and signal:
            target = cash
            shares = target / px
            cash -= target + target * COMMISSION
            holding, peak = True, px
        elif holding and not signal:
            cash += shares * px * (1 - COMMISSION)
            shares, holding = 0.0, False
        cash *= (1 + CASH_APR / 252)
        values.iloc[i] = cash + shares * px
    return values


def build_universe(add_cyb):
    etfs, opens, _ = load_data()
    close_df = pd.DataFrame(etfs).sort_index().ffill()
    open_df = pd.DataFrame(opens).sort_index().reindex(close_df.index).ffill()
    if add_cyb:
        d = json.load(open(CYB_CACHE))
        cyb = pd.Series(d["close"], index=pd.to_datetime(d["dates"])).sort_index()
        nav = cyb_timing_nav(cyb)
        close_df[CYB_CODE] = nav.reindex(close_df.index).ffill()
        open_df[CYB_CODE] = close_df[CYB_CODE]
    return close_df, open_df


def run_v7(close_df, open_df):
    values, trades = rotation_backtest(close_df, open_df=open_df)
    benches = {}
    for name, s in load_benches().items():
        s = s.dropna().reindex(values.index, method="ffill").dropna()
        common = values.dropna().index.intersection(s.index)
        benches[name] = s.reindex(common).ffill() if len(common) else None
    common = values.dropna().index
    sv = values.reindex(common).ffill()
    benches = {k: v for k, v in benches.items() if v is not None}
    return sv, benches, trades


def run_split(close_df, open_df, cyb_w, cyb_nav):
    """轮动仓拆 CYB_W 给 v4.1, 其余 v7 轮动。总净值 = (1-cyb_w)*v7_nav + cyb_w*cyb_nav。"""
    v7_sv, benches, trades = run_v7(close_df, open_df)
    cyb = cyb_nav.reindex(v7_sv.index).ffill()
    cyb = cyb / cyb.iloc[0]
    sv = (1 - cyb_w) * v7_sv + cyb_w * cyb
    return sv, benches, trades


def run_gate(close_df, open_df, cyb_raw):
    """C 牛熊门控: 牛(创业板>MA300)满仓创业板v4.1, 熊切换去跑v7轮动, 切换扣双向费。"""
    v7_sv, benches, trades = run_v7(close_df, open_df)
    sig = cyb_raw.shift(1)
    hold = sig > cyb_raw.rolling(CYB_MA).mean()
    cyb_nav = cyb_timing_nav(cyb_raw)
    idx = cyb_nav.index.intersection(v7_sv.index)
    n1 = (cyb_nav.reindex(idx).ffill() / cyb_nav.reindex(idx).ffill().iloc[0])
    n2 = (v7_sv.reindex(idx).ffill() / v7_sv.reindex(idx).ffill().iloc[0])
    h = hold.reindex(idx).ffill().astype(bool)

    out = pd.Series(index=idx, dtype=float)
    cur, n_switch = None, 0
    for i, date in enumerate(idx):
        regime = bool(h.iloc[i])
        prev = out.iloc[i - 1] if i > 0 else 1.0
        if i > 0 and regime != cur:
            prev *= (1 - 2 * COMMISSION)
            n_switch += 1
        n = n1 if regime else n2
        out.iloc[i] = prev * (n.iloc[i] / n.iloc[i - 1]) if i > 0 else 1.0
        cur = regime
    print(f"      C 门控切换 {n_switch} 次")
    return out, benches, trades


def main():
    print("[1/2] 读取缓存...")
    base_close, base_open = build_universe(False)
    mix_close, mix_open = build_universe(True)
    d = json.load(open(CYB_CACHE))
    cyb_raw = pd.Series(d["close"], index=pd.to_datetime(d["dates"])).sort_index()
    cyb_nav = cyb_timing_nav(cyb_raw)

    names = {CYB_CODE: CYB_NAME}
    for f in glob.glob(os.path.join(ETFS_DIR, "*.json")):
        j = json.load(open(f))
        names[j["code"]] = j["name"]

    print("[2/2] 回测对比...")
    configs = [("base",  "v7原版",            base_close, base_open, None),
               ("mix",   "A 入池(第20候选)",   mix_close,  mix_open,  None)]
    for label, desc, cdf, odf, _ in configs:
        sv, benches, trades = run_v7(cdf, odf)
        sm = calc_metrics(sv)
        n_buy = sum(1 for t in trades if t["action"] == "buy")
        n_sw = sum(1 for t in trades if t["action"] == "switch")
        n_cyb = sum(1 for t in trades if t["code"] == CYB_CODE)
        bench_str = "  ".join(f"{n}:{calc_metrics(b).get('annual_return', 0):.1f}%" for n, b in benches.items())
        print(f"  {label:5s} {desc:18s} 年化 {sm['annual_return']:6.1f}%  夏普 {sm['sharpe']:.2f}  "
              f"回撤 {sm['max_drawdown']:5.1f}%  总收益 {sm['total_return']:6.1f}%  "
              f"买入{n_buy} 切换{n_sw} 选创业板{n_cyb}  ({bench_str})")

    for cyb_w in (0.10, 0.20, 0.30):
        sv, benches, trades = run_split(base_close, base_open, cyb_w, cyb_nav)
        sm = calc_metrics(sv)
        bench_str = "  ".join(f"{n}:{calc_metrics(b).get('annual_return', 0):.1f}%" for n, b in benches.items())
        print(f"  split {f'B 拆仓{cyb_w:.0%}给v4.1':14s} 年化 {sm['annual_return']:6.1f}%  夏普 {sm['sharpe']:.2f}  "
              f"回撤 {sm['max_drawdown']:5.1f}%  总收益 {sm['total_return']:6.1f}%  ({bench_str})")

    gate_sv, gate_benches, gate_trades = run_gate(base_close, base_open, cyb_raw)
    gm = calc_metrics(gate_sv)
    bench_str = "  ".join(f"{n}:{calc_metrics(b).get('annual_return', 0):.1f}%" for n, b in gate_benches.items())
    print(f"  gate {f'C 牛熊门控(MA{CYB_MA})':14s} 年化 {gm['annual_return']:6.1f}%  夏普 {gm['sharpe']:.2f}  "
          f"回撤 {gm['max_drawdown']:5.1f}%  总收益 {gm['total_return']:6.1f}%  ({bench_str})")

    sv, benches, trades = gate_sv, gate_benches, gate_trades
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    html_file = os.path.join(OUTPUT_DIR, f"etf_rot_signal_cyb_result_{timestamp}.html")
    with open(html_file, "w", encoding="utf-8") as f:
        f.write(plot_results(sv, benches, trades, names, timestamp))
    print(f"      HTML -> {html_file}")
    pd.DataFrame({"date": sv.index, "strategy": sv.values,
                  **{n: bv.reindex(sv.index).ffill().values for n, bv in benches.items()}}).to_csv(
        os.path.join(OUTPUT_DIR, f"etf_rot_signal_cyb_result_{timestamp}.csv"),
        index=False, encoding="utf-8-sig")
    print(f"      CSV -> {os.path.join(OUTPUT_DIR, f'etf_rot_signal_cyb_result_{timestamp}.csv')}")


if __name__ == "__main__":
    main()
