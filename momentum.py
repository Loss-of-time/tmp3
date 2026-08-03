#!/usr/bin/env python3
"""单票动量策略回测。

只在调仓日(每 REBAL 交易日)检视一次: 从"动量 > 0 且价格 >= MIN_PX 且具备
MA_N 日均线数据"的股票里挑过去 LOOKBACK 日涨幅最高(跳过最近 SKIP 日)的 1 只
满仓买入(资金量小, 单票)。买入不需要站上均线。持仓期间每日检查离场:
跌破 MA_N 均线(趋势破坏)或从持仓峰值回撤 TRAIL 即卖出。不在股间频繁换仓,
空仓等下一个调仓日出现新信号再买入。min_px 过滤低价股(2-3元小票常以
连板/炒作为主, 是动量排名的噪音源, 不用于离场判断)。

回测区间 2020-01 ~ 2026-07, 基准沪深300, 成本万三双向+印花税万五。
复用 wyckoff 缓存 (cache_wyckoff/stocks/, 2016起后复权日线)。
"""

import sys
from datetime import datetime

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

pd.DataFrame.append = lambda self, other, ignore_index=False, sort=False: pd.concat(
    [self, other], ignore_index=ignore_index, sort=sort
)

import baostock as bs
import wyckoff as w
from backtest import calc_metrics

# --- config ---
LOOKBACK = 40          # 动量窗口(交易日)
SKIP = 5               # 跳过最近 N 日, 避开短期反转
TRAIL = 0.35           # 移动止损: 从持仓期最高收盘回撤此比例清仓
MA_N = 200             # 趋势过滤: 只买股价 > MA_N 的股票; 跌破则视为趋势破坏卖出
MIN_PX = 10            # 剔除低于此价位的股票(低价炒作票噪音)
REBAL = 10             # 每 N 个交易日检视买入一次 (10≈双周)
POS_WEIGHT = 1.0       # 单票仓位
BT_START = "2020-01-01"
CASH_APR = 0.02


def load_data():
    print("[1/4] 成分股/行业/名称...")
    index_stocks = w.get_index_stocks()
    codes = list(index_stocks.keys())
    all_names = w.get_stock_names()
    ind_map = w.get_industry_map()
    for code in list(codes):
        name = all_names.get(code, "")
        ind = ind_map.get(code, "")
        if ind in w.EXCLUDE_CSRC or "ST" in name:
            codes.remove(code)
    print(f"      {len(codes)} 只")

    print("[2/4] OHLCV (缓存)...")
    all_data = w.fetch_all_stocks(codes)
    cutoff = pd.Timestamp(w.DATA_MIN)
    dfs = {}
    for code, data in all_data.items():
        df = pd.DataFrame({k: data[k] for k in ["date", "open", "high", "low", "close", "volume"]})
        df["date"] = pd.to_datetime(df["date"])
        df = df.set_index("date")
        if df.index[0] <= cutoff:
            dfs[code] = df
    print(f"      {len(dfs)} 只有效")

    print("[3/4] 基准沪深300...")
    hs300_data = w.fetch_benchmark(w.HS300_CODE, "hs300")
    hs300_s = pd.Series(hs300_data["close"], index=pd.to_datetime(hs300_data["dates"])).sort_index()
    print("[4/4] 就绪")
    return dfs, hs300_s


def momentum_backtest(dfs, bt_start=BT_START, lookback=LOOKBACK, skip=SKIP,
                      trail=TRAIL, ma_n=MA_N, min_px=MIN_PX, rebal=REBAL,
                      pos_weight=POS_WEIGHT):
    """单票时间序列动量回测 (持仓一只, 不换仓, 只靠止损/趋势破坏离场)。

    返回 (values, trades).
    """
    # bt 交易日 = 各股交易日并集, 裁剪到回测区间
    bt_dates = pd.DatetimeIndex([])
    for df in dfs.values():
        bt_dates = bt_dates.union(df.index)
    bt_dates = bt_dates[(bt_dates >= pd.Timestamp(bt_start)) & (bt_dates <= pd.Timestamp(w.END))].sort_values()

    close = pd.DataFrame({c: df["close"] for c, df in dfs.items()}).reindex(bt_dates).ffill()

    # 动量矩阵: t 日的动量 = 收盘价[shift(skip) 处] / LOOKBACK 日前 - 1
    mom = close.shift(skip).pct_change(lookback)
    # 趋势线(仅用于离场: 收盘跌破 MA_N 视为趋势破坏)与买入池
    ma = close.rolling(ma_n, min_periods=ma_n).mean()
    above_ma = close > ma
    eligible = ma.notna() & (close >= min_px)

    values = pd.Series(index=bt_dates, dtype=float)
    cash = 1.0
    pos = None          # {"code","shares","entry_price","entry_date","peak"}
    trades = []

    for i, date in enumerate(bt_dates):
        if i == 0:
            values.iloc[i] = cash
            continue
        is_rebal = (i % rebal == 0)
        row = mom.loc[date][eligible.loc[date]].dropna()
        best = row.idxmax() if not row.empty else None
        best_mom = row[best] if best is not None else np.nan
        has_signal = best is not None and best_mom > 0

        # 先处理持仓: 移动止损 + 趋势破坏 (每日检查)
        if pos is not None:
            px = close.loc[date, pos["code"]]
            if not np.isnan(px):
                pos["peak"] = max(pos["peak"], px)
                trend_ok = above_ma.loc[date, pos["code"]]
                reason = None
                if px <= pos["peak"] * (1 - trail):
                    reason = "trail"
                elif not trend_ok:
                    reason = "trend"
                if reason:
                    proceeds = pos["shares"] * px * (1 - w.COMMISSION - w.STAMP_TAX)
                    cash += proceeds
                    trades.append({
                        "code": pos["code"], "entry_date": pos["entry_date"],
                        "entry_price": pos["entry_price"], "exit_date": date,
                        "exit_price": px, "reason": reason,
                        "pnl_pct": (px / pos["entry_price"] - 1) * 100,
                        "days": (date - pos["entry_date"]).days,
                    })
                    pos = None

        # 买入决策: 仅在调仓日, 且空仓时
        if is_rebal and pos is None and has_signal:
            px = close.loc[date, best]
            target_amt = cash * pos_weight
            shares = target_amt / px
            cost = target_amt * w.COMMISSION
            cash -= target_amt + cost
            pos = {"code": best, "shares": shares, "entry_price": px,
                   "entry_date": date, "peak": px}

        # 市值
        mv = cash
        if pos is not None:
            mv += pos["shares"] * close.loc[date, pos["code"]]
        cash *= (1 + CASH_APR / 252)
        values.iloc[i] = mv

    # 期末平仓
    if pos is not None:
        px = close.loc[bt_dates[-1], pos["code"]]
        trades.append({
            "code": pos["code"], "entry_date": pos["entry_date"],
            "entry_price": pos["entry_price"], "exit_date": bt_dates[-1],
            "exit_price": px, "reason": "end",
            "pnl_pct": (px / pos["entry_price"] - 1) * 100,
            "days": (bt_dates[-1] - pos["entry_date"]).days,
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

    fig.add_trace(go.Table(
        header=dict(values=["指标", "策略", "沪深300"]),
        cells=dict(values=[
            ["年化收益 %", "夏普", "最大回撤 %", "交易笔数", "胜率 %", "平均盈利 %", "平均亏损 %", "平均持仓天数"],
            [f"{sm.get('annual_return',0):.1f}", f"{sm.get('sharpe',0):.2f}",
             f"{sm.get('max_drawdown',0):.1f}", f"{n_t}", f"{win_rate:.1f}",
             f"{avg_win:.1f}", f"{avg_loss:.1f}", f"{avg_days:.0f}"],
            [f"{bm.get('annual_return',0):.1f}", f"{bm.get('sharpe',0):.2f}",
             f"{bm.get('max_drawdown',0):.1f}", "-", "-", "-", "-", "-"],
        ]),
    ), row=3, col=2)

    fig.update_layout(
        title_text=f"单票动量策略回测 ({timestamp})<br><sup>"
                   f"动量{LOOKBACK}日(跳{SKIP}日)+{MA_N}日趋势过滤 | "
                   f"移动止损{int(TRAIL*100)}% | 单票{int(POS_WEIGHT*100)}% | 2020-2026</sup>",
        height=1000,
    )
    fig.update_yaxes(title_text="净值", row=1, col=1)
    fig.update_yaxes(title_text="年度收益 (%)", row=2, col=1)
    fig.update_yaxes(title_text="单笔盈亏 (%)", row=3, col=1)
    return fig.to_html(include_plotlyjs="cdn", full_html=True)


def main():
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    lg = bs.login()
    if lg.error_code != "0":
        print(f"baostock login failed: {lg.error_msg}", file=sys.stderr)
        sys.exit(1)

    dfs, hs300_s = load_data()
    print(f"[5/6] 回测 (lookback={LOOKBACK}, trail={TRAIL})...")
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

    print("\n--- 回测结果 ---")
    print(f"      策略年化收益: {sm['annual_return']:.1f}%")
    print(f"      沪深300年化收益: {bm['annual_return']:.1f}%")
    print(f"      策略夏普: {sm['sharpe']:.2f}")
    print(f"      策略最大回撤: {sm['max_drawdown']:.1f}%")
    print(f"      总收益: {sm['total_return']:.1f}%")
    print(f"      交易笔数: {n_t}  胜率: {win_rate:.1f}%")

    csv_file = f"momentum_result_{timestamp}.csv"
    pd.DataFrame({"date": sv.index, "strategy": sv.values, "hs300": bv.values}).to_csv(
        csv_file, index=False, encoding="utf-8-sig")
    print(f"      CSV -> {csv_file}")

    if n_t:
        tdf.sort_values("exit_date").to_csv(f"momentum_trades_{timestamp}.csv",
                                            index=False, encoding="utf-8-sig")
        print(f"      交易明细 -> momentum_trades_{timestamp}.csv")

    html_file = f"momentum_result_{timestamp}.html"
    html = plot_results(sv, bv, trades, timestamp)
    with open(html_file, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"      HTML -> {html_file}")

    bs.logout()
    print("\n完成.")


if __name__ == "__main__":
    main()
