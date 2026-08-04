#!/usr/bin/env python3
"""动量趋势多票回测 (v3 改: 最多 MAX_HOLD 只等权)。

在调仓日(每 REBAL 交易日)从"动量>0 且价格>=MIN_PX 且具备 MA_N 均线"的股票里
按动量排序买入补足空位 (每只 POS_WEIGHT 等权, 最多 MAX_HOLD 只)。持仓每日检查
离场: 跌破 MA_N(趋势破坏)或自峰值回撤 TRAIL(移动止损)即卖出。
追求"多数小亏、少数大赚": 移动止损截断亏损, 让趋势股跑出翻倍段。

回测区间 2020-01 ~ 2026-07, 基准沪深300 (cache_bt/hs300.json 离线),
成本万三双向+印花税万五。复用 cache_wyckoff/stocks/ 缓存 (后复权日线, 纯离线)。
"""

import glob
import json
import os
from datetime import datetime

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from backtest import calc_metrics

# --- config ---
LOOKBACK = 20          # 动量窗口(交易日) (扫 20/40: 20 明显更优)
SKIP = 5               # 跳过最近 N 日, 避开短期反转
TRAIL = 0.15           # 移动止损: 从持仓期最高收盘回撤此比例清仓
MA_N = 100             # 趋势过滤: 只买股价 > MA_N 的股票; 跌破则视为趋势破坏卖出
MIN_PX = 10            # 剔除低于此价位的股票(低价炒作票噪音)
REBAL = 10             # 每 N 个交易日检视买入一次 (10≈双周)
MAX_HOLD = 5           # 最多同时持仓数
POS_WEIGHT = 0.20      # 单票目标仓位 (等权)
BT_START = "2020-01-01"
BT_END = "2026-07-31"
DATA_MIN = "2018-01-01"   # 股票数据起点须早于此时(保证预热充足)
CASH_APR = 0.02
COMMISSION = 0.0003
STAMP_TAX = 0.0005

STOCK_CACHE = "cache_wyckoff/stocks"
HS300_CACHE = "cache_bt/hs300.json"


def load_data():
    print("[1/2] OHLCV (缓存, 纯离线)...")
    dfs = {}
    for f in glob.glob(os.path.join(STOCK_CACHE, "*.json")):
        d = json.load(open(f))
        df = pd.DataFrame({k: d[k] for k in ["date", "open", "high", "low", "close", "volume"]})
        df["date"] = pd.to_datetime(df["date"])
        df = df.set_index("date")
        if df.index[0] <= pd.Timestamp(DATA_MIN):
            dfs[d["code"]] = df
    print(f"      {len(dfs)} 只有效")

    print("[2/2] 基准沪深300...")
    hs300 = json.load(open(HS300_CACHE))
    hs300_s = pd.Series(hs300["close"], index=pd.to_datetime(hs300["dates"])).sort_index()
    return dfs, hs300_s


def momentum_backtest(dfs, bt_start=BT_START, lookback=LOOKBACK, skip=SKIP,
                      trail=TRAIL, ma_n=MA_N, min_px=MIN_PX, rebal=REBAL,
                      max_hold=MAX_HOLD, pos_weight=POS_WEIGHT):
    """动量多票回测 (最多 max_hold 只等权, 每日止损/趋势破坏离场)。

    返回 (values, trades).
    """
    bt_dates = pd.DatetimeIndex([])
    for df in dfs.values():
        bt_dates = bt_dates.union(df.index)
    bt_dates = bt_dates[(bt_dates >= pd.Timestamp(bt_start)) & (bt_dates <= pd.Timestamp(BT_END))].sort_values()

    close = pd.DataFrame({c: df["close"] for c, df in dfs.items()}).reindex(bt_dates).ffill()

    mom = close.shift(skip).pct_change(lookback)
    ma = close.rolling(ma_n, min_periods=ma_n).mean()
    above_ma = close > ma
    eligible = ma.notna() & (close >= min_px)

    values = pd.Series(index=bt_dates, dtype=float)
    cash = 1.0
    pos = {}   # code -> {"shares","entry_price","entry_date","peak"}
    trades = []

    for i, date in enumerate(bt_dates):
        if i == 0:
            values.iloc[i] = cash
            continue
        is_rebal = (i % rebal == 0)

        # 每日离场检查: 移动止损 + 趋势破坏
        for code in list(pos):
            px = close.loc[date, code]
            if np.isnan(px):
                continue
            p = pos[code]
            p["peak"] = max(p["peak"], px)
            reason = None
            if px <= p["peak"] * (1 - trail):
                reason = "trail"
            elif not above_ma.loc[date, code]:
                reason = "trend"
            if reason:
                proceeds = p["shares"] * px * (1 - COMMISSION - STAMP_TAX)
                cash += proceeds
                trades.append({
                    "code": code, "entry_date": p["entry_date"],
                    "entry_price": p["entry_price"], "exit_date": date,
                    "exit_price": px, "reason": reason,
                    "pnl_pct": (px / p["entry_price"] - 1) * 100,
                    "days": (date - p["entry_date"]).days,
                })
                del pos[code]

        # 调仓日: 按动量补足空位
        if is_rebal:
            free = max_hold - len(pos)
            if free > 0:
                row = mom.loc[date][eligible.loc[date]].dropna()
                held = set(pos)
                cand = row[~row.index.isin(held)].sort_values(ascending=False)
                for code in cand.index[:free]:
                    if cand[code] <= 0:
                        break
                    px = close.loc[date, code]
                    if np.isnan(px):
                        continue
                    equity = cash + sum(p["shares"] * close.loc[date, c]
                                        for c, p in pos.items() if not np.isnan(close.loc[date, c]))
                    target = min(equity * pos_weight, cash)
                    shares = target / px
                    cost = target * COMMISSION
                    cash -= target + cost
                    pos[code] = {"shares": shares, "entry_price": px,
                                 "entry_date": date, "peak": px}

        # 市值
        mv = cash + sum(p["shares"] * close.loc[date, c]
                        for c, p in pos.items() if not np.isnan(close.loc[date, c]))
        cash *= (1 + CASH_APR / 252)
        values.iloc[i] = mv

    # 期末平仓
    for code, p in list(pos.items()):
        px = close.loc[bt_dates[-1], code]
        trades.append({
            "code": code, "entry_date": p["entry_date"],
            "entry_price": p["entry_price"], "exit_date": bt_dates[-1],
            "exit_price": px, "reason": "end",
            "pnl_pct": (px / p["entry_price"] - 1) * 100,
            "days": (bt_dates[-1] - p["entry_date"]).days,
        })

    return values, trades


def plot_results(sv, bv, trades, timestamp):
    fig = make_subplots(
        rows=3, cols=2,
        subplot_titles=("净值曲线", "年度收益对比", "交易盈亏分布", "交易统计"),
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

    wins = [t for t in trades if t["pnl_pct"] >= 0]
    losses = [t for t in trades if t["pnl_pct"] < 0]
    fig.add_trace(go.Scatter(x=[t["exit_date"] for t in wins], y=[t["pnl_pct"] for t in wins],
                             mode="markers", name=f"盈利 ({len(wins)})",
                             marker=dict(color="green", size=8, symbol="triangle-up")), row=3, col=1)
    fig.add_trace(go.Scatter(x=[t["exit_date"] for t in losses], y=[t["pnl_pct"] for t in losses],
                             mode="markers", name=f"亏损 ({len(losses)})",
                             marker=dict(color="red", size=8, symbol="triangle-down")), row=3, col=1)

    sm = calc_metrics(sv)
    bm = calc_metrics(bv)
    tdf = pd.DataFrame(trades)
    n_t = len(tdf)
    win_rate = (tdf["pnl_pct"] > 0).mean() * 100 if n_t else 0
    avg_win = tdf.loc[tdf["pnl_pct"] > 0, "pnl_pct"].mean() if (tdf["pnl_pct"] > 0).any() else 0
    avg_loss = tdf.loc[tdf["pnl_pct"] < 0, "pnl_pct"].mean() if (tdf["pnl_pct"] < 0).any() else 0
    avg_days = tdf["days"].mean() if n_t else 0
    n_double = (tdf["pnl_pct"] >= 100).sum()

    fig.add_trace(go.Table(
        header=dict(values=["指标", "策略", "沪深300"]),
        cells=dict(values=[
            ["年化收益 %", "夏普", "最大回撤 %", "交易笔数", "胜率 %", "平均盈利 %", "平均亏损 %", "翻倍笔数"],
            [f"{sm.get('annual_return',0):.1f}", f"{sm.get('sharpe',0):.2f}",
             f"{sm.get('max_drawdown',0):.1f}", f"{n_t}", f"{win_rate:.1f}",
             f"{avg_win:.1f}", f"{avg_loss:.1f}", f"{n_double}"],
            [f"{bm.get('annual_return',0):.1f}", f"{bm.get('sharpe',0):.2f}",
             f"{bm.get('max_drawdown',0):.1f}", "-", "-", "-", "-", "-"],
        ]),
    ), row=3, col=2)

    fig.update_layout(
        title_text=f"动量趋势多票回测 ({timestamp})<br><sup>"
                   f"动量{LOOKBACK}日(跳{SKIP}日)+{MA_N}日趋势过滤 | 移动止损{int(TRAIL*100)}% | "
                   f"最多{MAX_HOLD}只等权{int(POS_WEIGHT*100)}% | 2020-2026</sup>",
        height=1000,
    )
    fig.update_yaxes(title_text="净值", row=1, col=1)
    fig.update_yaxes(title_text="年度收益 (%)", row=2, col=1)
    fig.update_yaxes(title_text="单笔盈亏 (%)", row=3, col=1)
    return fig.to_html(include_plotlyjs="cdn", full_html=True)


def main():
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    dfs, hs300_s = load_data()
    print(f"[3/4] 回测 (lookback={LOOKBACK}, trail={TRAIL}, max_hold={MAX_HOLD})...")
    values, trades = momentum_backtest(dfs)

    hs300_s = hs300_s.reindex(values.index).ffill()
    common = values.dropna().index.intersection(hs300_s.dropna().index)
    sv = values.reindex(common).ffill()
    bv = hs300_s.reindex(common).ffill()

    sm = calc_metrics(sv)
    bm = calc_metrics(bv)
    tdf = pd.DataFrame(trades)
    n_t = len(tdf)
    win_rate = (tdf["pnl_pct"] > 0).mean() * 100 if n_t else 0
    wins = tdf[tdf["pnl_pct"] > 0]["pnl_pct"]
    losses = tdf[tdf["pnl_pct"] < 0]["pnl_pct"]

    print("\n--- 回测结果 ---")
    print(f"      策略年化收益: {sm['annual_return']:.1f}%")
    print(f"      沪深300年化收益: {bm['annual_return']:.1f}%")
    print(f"      策略夏普: {sm['sharpe']:.2f}")
    print(f"      策略最大回撤: {sm['max_drawdown']:.1f}%")
    print(f"      总收益: {sm['total_return']:.1f}%")
    print(f"      交易笔数: {n_t}  胜率: {win_rate:.1f}%")
    print(f"      交易分布: 均赢{np.mean(wins):.1f}% 均亏{np.mean(losses):.1f}% "
          f"最大单笔{tdf['pnl_pct'].max():.1f}% 翻倍{(tdf['pnl_pct']>=100).sum()}笔")

    csv_file = f"momentum_top5_result_{timestamp}.csv"
    pd.DataFrame({"date": sv.index, "strategy": sv.values, "hs300": bv.values}).to_csv(
        csv_file, index=False, encoding="utf-8-sig")
    print(f"      CSV -> {csv_file}")

    if n_t:
        tdf.sort_values("exit_date").to_csv(f"momentum_top5_trades_{timestamp}.csv",
                                            index=False, encoding="utf-8-sig")
        print(f"      交易明细 -> momentum_top5_trades_{timestamp}.csv")

    html_file = f"momentum_top5_result_{timestamp}.html"
    html = plot_results(sv, bv, trades, timestamp)
    with open(html_file, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"      HTML -> {html_file}")
    print("\n完成.")


if __name__ == "__main__":
    main()
