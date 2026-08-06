#!/usr/bin/env python3
"""调参后 (tp_half=0.8 / tp_frac=1.0 / base_w=0.45) 动态池长回测报告。

对比调参前 (无止盈) 输出完整 HTML 报告: 净值曲线+持仓甘特+年度收益+指标表。
"""
import os
import sys
from datetime import datetime

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backtest import COMMISSION, calc_metrics
from rot_core import rotation_sim, BASE_ETF, MA_N, LOOKBACK, MOM_GAP, TRAIL, COOLDOWN
from etf_rot_signal import load_benches, plot_results
from etf_rot_dynpool import load_candidates, fetch_aum, build_pool

TS = datetime.now().strftime("%Y%m%d_%H%M%S")


def main():
    print("[1/3] 构建动态池...")
    cands = load_candidates()
    aum = fetch_aum()
    close_df, open_df, log = build_pool(cands, aum)
    admitted = [l for l in log if l[4] == "admit"]
    names = {code: c["name"] for code, c in cands.items()}
    print(f"      入池 {len(admitted)}/{len(cands)} 只")

    print("[2/3] 双版本回测...")
    benchs = {}
    for bname, s in load_benches().items():
        benchs[bname] = s.dropna().reindex(close_df.index, method="ffill").dropna()

    def bt(tp_half, tp_frac, label):
        sim = rotation_sim(close_df, open_df, base_w=0.45, base_etf=BASE_ETF,
                           commission=COMMISSION, start="2015-01-01",
                           tp_half=tp_half, tp_frac=tp_frac)
        sv = pd.Series(sim["navs"], index=sim["dates"])
        sm = calc_metrics(sv)
        print(f"      [{label}] 年化 {sm['annual_return']:.1f}% 夏普 {sm['sharpe']:.2f} "
              f"回撤 {sm['max_drawdown']:.1f}% 总收益 {sm['total_return']:.0f}% | "
              f"买入{sum(1 for t in sim['trades'] if t['action']=='buy')} "
              f"切换{sum(1 for t in sim['trades'] if t['action']=='switch')}")
        return sv, sim["trades"]

    sv_new, trades_new = bt(0.8, 1.0, "调参后 0.8/1.0")
    sv_old, trades_old = bt(None, 0.5, "调参前 无止盈")

    print("[3/3] 输出 HTML...")
    title = (f"动态池回测: 调参后 tp_half=0.8/tp_frac=1.0 ({TS})<br><sup>"
             f"动态池(上市满3年/AUM≥10亿/相关<0.8/只进不出, 入池{len(admitted)}只) | "
             f"MA{MA_N}+动量{LOOKBACK} 动量差>{MOM_GAP:.0%} 止损{TRAIL:.0%} 冷却{COOLDOWN}日 | "
             f"底仓{BASE_ETF} 45% | 调参前基线: "
             f"{calc_metrics(sv_old)['annual_return']:.1f}%/夏普{calc_metrics(sv_old)['sharpe']:.2f}/"
             f"回撤{calc_metrics(sv_old)['max_drawdown']:.1f}% | 2015 ~ {sv_new.index[-1].strftime('%Y-%m')}</sup>")
    html = f"etf_rot_dynpool_report_{TS}.html"
    with open(html, "w", encoding="utf-8") as f:
        f.write(plot_results(sv_new, benchs, trades_new, names, TS, title_text=title))
    print(f"      HTML -> {html}")


if __name__ == "__main__":
    main()
