#!/usr/bin/env python3
"""行业板块 ETF 动量轮动 + 牛熊开关回测 (v5)。

只在调仓日 (每 REBAL 交易日) 检视: 从"收盘 > MA_N 牛熊线"的行业 ETF 里挑过去
LOOKBACK 日动量最强的一只持仓; 都不站上则空仓吃现金 CASH_APR。持仓期间每日
检查: 跌破 MA_N (趋势破坏) 或从持仓峰值回撤 TRAIL (移动止损) 立即离场, 等
下一个调仓日再进。不在板块间频繁切换 (v3 教训: 高频追最热必亏)。

BASE_W 比例的仓位始终持有低波底仓 (红利低波 ETF), 稀释单板块暴涨暴跌 (2026-06
半导体 +47% 后 7 月 -33% 全回吐是纯轮动主要回撤来源); 剩余资金做板块轮动。

数据: cache_bt/etf_industry/{code}.json (东财 qfq 前复权日线)。基准沪深300。
回测区间 2020-01 ~ 2026-07, 成本万三双向。
"""

import glob
import json
import os
from datetime import datetime

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from backtest import COMMISSION, calc_metrics

# --- config ---
OUTPUT_DIR = "output"   # 统一输出目录
MA_N = 200          # 牛熊线: 只买收盘 > MA_N 的 ETF, 跌破则趋势破坏离场
LOOKBACK = 180      # 动量窗口(交易日), 长动量更稳 (扫参最优)
REBAL = 40          # 每 N 个交易日检视切换一次 (低频)
TRAIL = 0.20        # 移动止损: 从持仓峰值回撤此比例离场
BASE_W = 0.45       # 低波底仓权重: 此比例资金始终持有低波ETF, 剩余做轮动
BASE_ETF = "512890" # 红利低波 ETF
BT_START = "2020-01-01"
CASH_APR = 0.02

ETFS_DIR = "cache_bt/etf_industry"
HS300_CACHE = "cache_bt/hs300.json"


def load_data():
    etfs = {}
    for f in sorted(glob.glob(os.path.join(ETFS_DIR, "*.json"))):
        d = json.load(open(f))
        etfs[d["code"]] = pd.Series(d["close"], index=pd.to_datetime(d["dates"])).sort_index()
    hs300 = json.load(open(HS300_CACHE))
    hs300_s = pd.Series(hs300["close"], index=pd.to_datetime(hs300["dates"])).sort_index()
    return etfs, hs300_s


def rotation_backtest(close_df, bt_start=BT_START, ma_n=MA_N, lookback=LOOKBACK,
                      rebal=REBAL, trail=TRAIL, base_w=BASE_W, base_etf=BASE_ETF):
    """动量轮动回测。返回 (values, trades). trades 记录买卖/切换/底仓动作。

    base_w 比例资金始终持有低波底仓 (base_etf), 剩余 (1-base_w) 做板块轮动。
    """
    bt = close_df[bt_start:]
    ma = bt.rolling(ma_n).mean()
    above = bt > ma
    mom = bt.pct_change(lookback)
    lowvol = bt[base_etf]

    values = pd.Series(index=bt.index, dtype=float)
    base_shares = base_w / lowvol.iloc[0]
    rot_cash = 1.0 - base_w
    shares = 0.0
    code = None
    peak = 0.0
    trades = []

    for i, date in enumerate(bt.index):
        if i == 0:
            values.iloc[i] = 1.0
            continue

        px = close_df.loc[date, code] if code else None
        is_rebal = (i % rebal == 0)
        signal = above.loc[date]

        # 轮动仓每日检查: 趋势破坏或移动止损
        if code is not None:
            peak = max(peak, px)
            reason = None
            if not bool(signal[code]):
                reason = "trend"
            elif px <= peak * (1 - trail):
                reason = "trail"
            if reason:
                proceeds = shares * px * (1 - COMMISSION)
                rot_cash += proceeds
                shares = 0.0
                code = None
                peak = 0.0
                trades.append({"date": date, "action": "sell", "code": None, "reason": reason})

        # 调仓日: 从站上 MA 的 ETF 里挑动量最强
        if is_rebal:
            elig = [c for c in signal[signal].index if not np.isnan(mom.loc[date, c])]
            if len(elig) > 0:
                best = mom.loc[date, elig].idxmax()
                best_px = close_df.loc[date, best]
                if code is None:
                    target = rot_cash
                    shares = target / best_px
                    cost = target * COMMISSION
                    rot_cash -= target + cost
                    code = best
                    peak = best_px
                    trades.append({"date": date, "action": "buy", "code": best,
                                   "reason": "new", "mom": round(float(mom.loc[date, best]) * 100, 1)})
                elif best != code:
                    old_code = code
                    proceeds = shares * px * (1 - COMMISSION)
                    rot_cash += proceeds
                    target = rot_cash
                    shares = target / best_px
                    cost = target * COMMISSION
                    rot_cash -= target + cost
                    code = best
                    peak = best_px
                    trades.append({"date": date, "action": "switch", "code": best,
                                   "reason": f"换 {old_code}", "mom": round(float(mom.loc[date, best]) * 100, 1)})

        px = close_df.loc[date, code] if code else None
        mv = base_shares * lowvol.iloc[i] + rot_cash + (shares * px if code else 0)
        values.iloc[i] = mv

    return values, trades


def plot_results(sv, bv, trades, names, timestamp):
    fig = make_subplots(
        rows=3, cols=2,
        subplot_titles=("净值曲线", "年度收益对比", "持仓标的", "策略指标"),
        row_heights=[0.4, 0.3, 0.3],
        specs=[[{"colspan": 2}, None], [{"colspan": 2}, None], [{}, {"type": "table"}]],
    )

    sv = sv / sv.iloc[0]
    bv = bv / bv.iloc[0]
    fig.add_trace(go.Scatter(x=sv.index, y=sv, mode="lines", name="策略",
                             line=dict(color="steelblue", width=2)), row=1, col=1)
    fig.add_trace(go.Scatter(x=bv.index, y=bv, mode="lines", name="沪深300",
                             line=dict(color="coral", width=1.5, dash="dash")), row=1, col=1)

    sy = sv.resample("YE").last().pct_change().dropna() * 100
    by_ = bv.resample("YE").last().pct_change().dropna() * 100
    years_labels = [str(d.year) for d in sy.index]
    fig.add_trace(go.Bar(x=years_labels, y=sy.values, name="策略", marker_color="steelblue"), row=2, col=1)
    fig.add_trace(go.Bar(x=years_labels, y=by_.values, name="沪深300", marker_color="coral", opacity=0.7), row=2, col=1)

    # 持仓标的甘特图: 用颜色区分板块, 标记持有区间
    holds = []
    cur = None
    start = None
    for t in trades:
        if t["action"] in ("buy", "switch"):
            if cur is not None and start is not None:
                holds.append((cur, start, t["date"]))
            cur = t["code"]
            start = t["date"]
        elif t["action"] == "sell":
            if cur is not None and start is not None:
                holds.append((cur, start, t["date"]))
            cur = None
    if cur is not None and start is not None:
        holds.append((cur, start, sv.index[-1]))
    for code, s, e in holds:
        fig.add_trace(go.Scatter(
            x=[s, e], y=[names[code], names[code]],
            mode="lines", line=dict(width=8), name=names[code], showlegend=False,
            hovertext=[f"{names[code]}: {s.date()} ~ {e.date()}"],
        ), row=3, col=1)

    sm = calc_metrics(sv)
    bm = calc_metrics(bv)
    n_buy = sum(1 for t in trades if t["action"] == "buy")
    n_switch = sum(1 for t in trades if t["action"] == "switch")

    fig.add_trace(go.Table(
        header=dict(values=["指标", "策略", "沪深300"]),
        cells=dict(values=[
            ["年化收益 %", "夏普", "最大回撤 %", "总收益 %", "买入次数", "切换次数", "轮动仓"],
            [f"{sm.get('annual_return',0):.1f}", f"{sm.get('sharpe',0):.2f}",
             f"{sm.get('max_drawdown',0):.1f}", f"{sm.get('total_return',0):.1f}",
             f"{n_buy}", f"{n_switch}", f"{int((1-BASE_W)*100)}%"],
            [f"{bm.get('annual_return',0):.1f}", f"{bm.get('sharpe',0):.2f}",
             f"{bm.get('max_drawdown',0):.1f}", f"{bm.get('total_return',0):.1f}", "-", "-", "-"],
        ]),
    ), row=3, col=2)

    fig.update_layout(
        title_text=f"行业ETF动量轮动回测 ({timestamp})<br><sup>"
                   f"站上MA{MA_N}的ETF里挑动量{LOOKBACK}日最强 | 每{REBAL}日检视 | "
                   f"低波底仓{int(BASE_W*100)}%+轮动{int((1-BASE_W)*100)}% 止损{int(TRAIL*100)}% | "
                   f"{BT_START} ~ 2026-07</sup>",
        height=1000,
    )
    fig.update_yaxes(title_text="净值", row=1, col=1)
    fig.update_yaxes(title_text="年度收益 (%)", row=2, col=1)
    fig.update_yaxes(title_text="持仓标的", row=3, col=1)
    return fig.to_html(include_plotlyjs="cdn", full_html=True)


def main():
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    print("[1/2] 读取缓存...")
    etfs, hs300_s = load_data()
    close_df = pd.DataFrame(etfs).sort_index().ffill()
    names = {json.load(open(f))["code"]: json.load(open(f))["name"]
             for f in glob.glob(os.path.join(ETFS_DIR, "*.json"))}
    print(f"      {len(etfs)} 只: " + ", ".join(names[c] for c in close_df.columns))

    print(f"[2/2] 回测 (MA{MA_N}, 动量{LOOKBACK}日, 每{REBAL}日检视, 止损{TRAIL:.0%}, 底仓{BASE_W:.0%})...")
    values, trades = rotation_backtest(close_df)

    hs300_s = hs300_s.reindex(values.index).ffill()
    common = values.dropna().index.intersection(hs300_s.dropna().index)
    sv = values.reindex(common).ffill()
    bv = hs300_s.reindex(common).ffill()

    sm = calc_metrics(sv)
    bm = calc_metrics(bv)
    n_buy = sum(1 for t in trades if t["action"] == "buy")
    n_switch = sum(1 for t in trades if t["action"] == "switch")

    print("\n--- 回测结果 ---")
    print(f"      策略年化收益: {sm['annual_return']:.1f}%")
    print(f"      沪深300年化收益: {bm['annual_return']:.1f}%")
    print(f"      策略夏普: {sm['sharpe']:.2f}")
    print(f"      策略最大回撤: {sm['max_drawdown']:.1f}%")
    print(f"      总收益: {sm['total_return']:.1f}%")
    print(f"      买入 {n_buy} 次, 切换 {n_switch} 次")

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    csv_file = os.path.join(OUTPUT_DIR, f"etf_rot_result_{timestamp}.csv")
    pd.DataFrame({"date": sv.index, "strategy": sv.values, "hs300": bv.values}).to_csv(
        csv_file, index=False, encoding="utf-8-sig")
    print(f"      CSV -> {csv_file}")

    if trades:
        pd.DataFrame(trades).to_csv(
            os.path.join(OUTPUT_DIR, f"etf_rot_trades_{timestamp}.csv"),
            index=False, encoding="utf-8-sig")
        print(f"      交易明细 -> {os.path.join(OUTPUT_DIR, f'etf_rot_trades_{timestamp}.csv')}")

    html_file = os.path.join(OUTPUT_DIR, f"etf_rot_result_{timestamp}.html")
    with open(html_file, "w", encoding="utf-8") as f:
        f.write(plot_results(sv, bv, trades, names, timestamp))
    print(f"      HTML -> {html_file}")
    print("\n完成.")


if __name__ == "__main__":
    main()
