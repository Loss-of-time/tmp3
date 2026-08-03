#!/usr/bin/env python3
"""Wyckoff accumulation/breakout strategy backtest for A-shares.

Signal (per article): long decline -> sideways -> selloff on volume (dump),
weak bounce on low volume, new low on much lower volume (test) ->
volume breakout above range -> buy. Stop below test low or -8%.
Exit: distribution (high volume, no progress), scale half then clear.
"""

import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

# ponytail: baostock uses removed pandas3 DataFrame.append
pd.DataFrame.append = lambda self, other, ignore_index=False, sort=False: pd.concat(
    [self, other], ignore_index=ignore_index, sort=sort
)

import baostock as bs
from backtest import (
    END, akshare_to_baostock, fetch_benchmark, get_index_stocks,
    get_industry_map, get_stock_names, EXCLUDE_CSRC,
)

# --- config ---
WY_CACHE = "cache_wyckoff"
STOCK_CACHE = os.path.join(WY_CACHE, "stocks")

START = "2016-01-01"      # data history window
BT_START = "2020-01-01"   # backtest window
DATA_MIN = "2018-01-01"   # stock must have data before this to compute setup

# signal params
DECLINE = 0.30          # drawdown from 250d high >= 30%
RANGE_LOOKBACK = 60     # sideways range window
RANGE_MAX_WIDE = 0.30   # max (high-low)/close over range window
DUMP_RET = -0.02        # dump day return <=
DUMP_VOL = 1.5          # dump volume >= x vma20
BOUNCE_VOL = 0.8        # bounce volume <= x vma20
TEST_VOL = 1.0          # test volume <= x vma20 (sweep: 1.0 best, 13.6% ann)
BREAK_VOL = 1.5         # breakout volume >= x vma20
BREAK_LOOKBACK = 60     # range high = max(close, prev N days)
TEST_LOOKBACK = 30      # days to find bounce/test after dump
BOUNCE_LOOKBACK = 10
BREAK_LOOKBACK_WIN = 20  # days to find breakout after test

STOP_LOW = 0.95         # stop = test low * this
MAX_LOSS = 0.92         # or entry * this, whichever higher
DIST_VOL = 2.0          # distribution: vol >= x vma20 and ret < DIST_RET
DIST_RET = 0.01
HUGE_VOL = 4.0          # clearance-sale volume multiple

MAX_HOLD = 5
POS_WEIGHT = 0.20       # per-stock weight
COMMISSION = 0.0003
STAMP_TAX = 0.0005
CASH_APR = 0.02

HS300_CODE = "sh.000300"

os.makedirs(STOCK_CACHE, exist_ok=True)


def fetch_one_stock(code):
    cache_file = os.path.join(STOCK_CACHE, f"{code}.json")
    if os.path.exists(cache_file):
        with open(cache_file) as f:
            return json.load(f)

    bs_code = akshare_to_baostock(code)
    try:
        rs = bs.query_history_k_data_plus(
            bs_code, "date,open,high,low,close,volume",
            start_date=START, end_date=END,
            frequency="d", adjustflag="3",
        )
        raw = rs.get_data()
    except Exception:
        return None

    if raw.empty:
        return None

    for col in ["open", "high", "low", "close", "volume"]:
        raw[col] = pd.to_numeric(raw[col], errors="coerce")
    raw = raw.dropna(subset=["open", "high", "low", "close", "volume"])

    if raw.empty:
        return None

    result = {"code": code}
    for col in ["date", "open", "high", "low", "close", "volume"]:
        result[col] = raw[col].tolist()
    result["volume"] = [float(x) for x in result["volume"]]

    with open(cache_file, "w") as f:
        json.dump(result, f)
    return result


def fetch_all_stocks(codes):
    results = {}
    total = len(codes)
    t0 = time.time()
    ok = 0
    fail = 0
    # ponytail: baostock socket 非线程安全, 必须串行
    for i, code in enumerate(codes, 1):
        r = fetch_one_stock(code)
        if r:
            results[r["code"]] = r
            ok += 1
        else:
            fail += 1
        if i % 50 == 0 or i == total:
            elapsed = time.time() - t0
            eta = elapsed / i * (total - i)
            print(f"      {i}/{total} ({i/total*100:.0f}%) 耗时:{elapsed:.0f}s 预计剩余:{eta:.0f}s")
    print(f"      成功 {ok}, 失败 {fail}")
    return results


# --- signal detection ---

def detect_setups(df):
    """Find buy setups on one stock's daily data.
    df: DataFrame indexed by date with open/high/low/close/volume.
    Returns list of dicts: {entry_idx, entry_price, stop, test_low, dump_date}.
    """
    close = df["close"].values
    high = df["high"].values
    low = df["low"].values
    vol = df["volume"].values
    n = len(df)

    vma = pd.Series(vol).rolling(20).mean().values
    ret = pd.Series(close).pct_change().values

    hi250 = pd.Series(close).rolling(250).max().shift(1).values
    range_hi = pd.Series(close).rolling(BREAK_LOOKBACK).max().shift(1).values
    range_hi_60 = pd.Series(high).rolling(RANGE_LOOKBACK).max().shift(1).values
    range_lo_60 = pd.Series(low).rolling(RANGE_LOOKBACK).min().shift(1).values

    setups = []
    i = 251
    while i < n - 1:
        if np.isnan(vma[i]) or np.isnan(hi250[i]) or hi250[i] <= 0:
            i += 1
            continue
        # long decline + sideways
        if close[i] > (1 - DECLINE) * hi250[i]:
            i += 1
            continue
        if np.isnan(range_hi_60[i]) or np.isnan(range_lo_60[i]) or range_hi_60[i] <= 0:
            i += 1
            continue
        if (range_hi_60[i] - range_lo_60[i]) / close[i] > RANGE_MAX_WIDE:
            i += 1
            continue

        # A: dump on volume
        if not (ret[i] <= DUMP_RET and vol[i] >= DUMP_VOL * vma[i]):
            i += 1
            continue
        dump_low = low[i]

        # B: weak bounce on low volume within BOUNCE_LOOKBACK days
        b = None
        for j in range(i + 1, min(i + BOUNCE_LOOKBACK + 1, n)):
            if ret[j] > 0 and vol[j] <= BOUNCE_VOL * vma[j]:
                b = j
                break
        if b is None:
            i += 1
            continue

        # C: new low on much lower volume (test)
        c = None
        for j in range(b + 1, min(b + TEST_LOOKBACK + 1, n)):
            if close[j] < dump_low and vol[j] <= TEST_VOL * vma[j]:
                c = j
                break
        if c is None:
            i += 1
            continue

        test_low = low[c]
        stop = max(test_low * STOP_LOW, close[c] * MAX_LOSS)

        # breakout: close above range high on volume, within window,
        # test low must hold (close stays above stop before breakout)
        e = None
        for j in range(c + 1, min(c + BREAK_LOOKBACK_WIN + 1, n)):
            if close[j] < stop:
                break  # test failed
            if (close[j] > range_hi[j] and close[j] > close[j - 1]
                    and vol[j] >= BREAK_VOL * vma[j]):
                e = j
                break
        if e is None:
            i = c + 1
            continue

        setups.append({
            "entry_idx": e,
            "entry_price": close[e],
            "stop": stop,
            "test_low": test_low,
            "dump_idx": i,
        })
        i = e + 1

    return setups


# --- backtest ---

def run_backtest(dfs, hs300_s, bt_dates):
    """Event-driven daily backtest. dfs: {code: df(OHLCV, indexed by date)}.
    Returns (portfolio_values, trades, daily_holdings_count).
    """
    # collect all setups across stocks
    all_setups = []  # (entry_date, code, setup)
    for code, df in dfs.items():
        for s in detect_setups(df):
            all_setups.append((df.index[s["entry_idx"]], code, s))
    all_setups.sort(key=lambda x: x[0])

    # holdings: code -> {shares, entry_price, stop, entry_date, half_sold}
    holdings = {}
    cash = 1.0
    trades = []  # closed trades

    # pre-compute daily arrays per stock for fast close/vol lookup
    # bt_dates = A股实际交易日并集 (不含节假日), 每只股票 ffilled, 裁剪到回测区间
    bt_dates = bt_dates.union(pd.DatetimeIndex([]))
    for df in dfs.values():
        bt_dates = bt_dates.union(df.index)
    bt_dates = bt_dates[(bt_dates >= pd.Timestamp(BT_START)) & (bt_dates <= pd.Timestamp(END))].sort_values()

    close_by = {}
    vol_by = {}
    vma_by = {}
    for code, df in dfs.items():
        close_by[code] = df["close"].reindex(bt_dates).ffill()
        vol_by[code] = df["volume"].reindex(bt_dates).ffill()
        vma_by[code] = df["volume"].rolling(20).mean().reindex(bt_dates).ffill()

    values = pd.Series(index=bt_dates, dtype=float)
    setup_ptr = 0
    n_setups = len(all_setups)

    for di, date in enumerate(bt_dates):
        if date in (close_by.get(next(iter(close_by)), pd.Series()).index):  # noop guard
            pass

        # exit signals first
        for code in list(holdings.keys()):
            if date not in close_by[code].index:
                continue
            px = close_by[code].loc[date]
            v = vol_by[code].loc[date]
            vm = vma_by[code].loc[date]
            if np.isnan(vm):
                vm = 0
            h = holdings[code]

            reason = None
            if px <= h["stop"]:
                reason = "stop"
            elif not np.isnan(v) and v >= HUGE_VOL * vm:
                reason = "huge_vol"
            elif not np.isnan(v) and v >= DIST_VOL * vm and h["px_prev"] is not None \
                    and (px - h["px_prev"]) / h["px_prev"] < DIST_RET:
                if h["half_sold"]:
                    reason = "distribution"
                else:
                    h["half_sold"] = True
                    sell_shares = h["shares"] / 2
                    proceeds = sell_shares * px * (1 - COMMISSION - STAMP_TAX)
                    cash += proceeds
                    h["shares"] -= sell_shares
                    trades.append({
                        "code": code, "entry_date": h["entry_date"], "entry_price": h["entry_price"],
                        "exit_date": date, "exit_price": px,
                        "shares": sell_shares, "reason": "half_dist",
                        "pnl_pct": (px / h["entry_price"] - 1) * 100,
                        "days": (date - h["entry_date"]).days,
                    })
                    h["px_prev"] = px
            if reason:
                proceeds = h["shares"] * px * (1 - COMMISSION - STAMP_TAX)
                cash += proceeds
                trades.append({
                    "code": code, "entry_date": h["entry_date"], "entry_price": h["entry_price"],
                    "exit_date": date, "exit_price": px,
                    "shares": h["shares"], "reason": reason,
                    "pnl_pct": (px / h["entry_price"] - 1) * 100,
                    "days": (date - h["entry_date"]).days,
                })
                del holdings[code]
                continue
            h["px_prev"] = px

        # buys
        while setup_ptr < n_setups and all_setups[setup_ptr][0] <= date:
            entry_date, code, s = all_setups[setup_ptr]
            setup_ptr += 1
            if code in holdings:
                continue
            if entry_date != date:
                continue
            if len(holdings) >= MAX_HOLD:
                continue
            px = close_by[code].loc[date]
            nav = cash + sum(h["shares"] * close_by[c].loc[date] for c, h in holdings.items())
            target_amt = min(cash, nav * POS_WEIGHT)
            if target_amt < 0.01:
                continue
            shares = target_amt / px
            cost = target_amt * COMMISSION
            cash -= target_amt + cost
            holdings[code] = {
                "shares": shares, "entry_price": px, "stop": s["stop"],
                "entry_date": date, "half_sold": False, "px_prev": px,
            }

        # mark to market
        mv = cash
        for code, h in holdings.items():
            if date in close_by[code].index:
                mv += h["shares"] * close_by[code].loc[date]
        # cash earns 2% apr
        cash *= (1 + CASH_APR / 252)
        values.iloc[di] = mv

    # close remaining at last price
    for code, h in holdings.items():
        px = close_by[code].dropna().iloc[-1]
        trades.append({
            "code": code, "entry_date": h["entry_date"], "entry_price": h["entry_price"],
            "exit_date": bt_dates[-1], "exit_price": px,
            "shares": h["shares"], "reason": "end",
            "pnl_pct": (px / h["entry_price"] - 1) * 100,
            "days": (bt_dates[-1] - h["entry_date"]).days,
        })

    return values, trades


# --- metrics (reuse from backtest.py) ---

from backtest import calc_metrics


# --- plot ---

def plot_results(strategy_values, hs300_values, trades, timestamp):
    fig = make_subplots(
        rows=3, cols=2,
        subplot_titles=("净值曲线", "年度收益对比", "交易盈亏分布", "交易统计"),
        row_heights=[0.4, 0.3, 0.3],
        specs=[[{"colspan": 2}, None], [{"colspan": 2}, None], [{}, {"type": "table"}]],
    )

    sv = strategy_values / strategy_values.iloc[0]
    bv = hs300_values / hs300_values.iloc[0]

    fig.add_trace(
        go.Scatter(x=sv.index, y=sv, mode="lines", name="策略",
                   line=dict(color="steelblue", width=2)),
        row=1, col=1,
    )
    fig.add_trace(
        go.Scatter(x=bv.index, y=bv, mode="lines", name="沪深300",
                   line=dict(color="coral", width=1.5, dash="dash")),
        row=1, col=1,
    )

    sy = sv.resample("YE").last().pct_change().dropna() * 100
    by_ = bv.resample("YE").last().pct_change().dropna() * 100
    years_labels = [str(d.year) for d in sy.index]
    fig.add_trace(
        go.Bar(x=years_labels, y=sy.values, name="策略", marker_color="steelblue"),
        row=2, col=1,
    )
    fig.add_trace(
        go.Bar(x=years_labels, y=by_.values, name="沪深300", marker_color="coral", opacity=0.7),
        row=2, col=1,
    )

    # trade scatter: win green / loss red
    wins = [t for t in trades if t["pnl_pct"] >= 0]
    losses = [t for t in trades if t["pnl_pct"] < 0]
    fig.add_trace(
        go.Scatter(
            x=[t["exit_date"] for t in wins], y=[t["pnl_pct"] for t in wins],
            mode="markers", name=f"盈利 ({len(wins)})",
            marker=dict(color="green", size=8, symbol="triangle-up"),
        ),
        row=3, col=1,
    )
    fig.add_trace(
        go.Scatter(
            x=[t["exit_date"] for t in losses], y=[t["pnl_pct"] for t in losses],
            mode="markers", name=f"亏损 ({len(losses)})",
            marker=dict(color="red", size=8, symbol="triangle-down"),
        ),
        row=3, col=1,
    )

    sm = calc_metrics(strategy_values)
    bm = calc_metrics(hs300_values)
    tdf = pd.DataFrame(trades)
    n_t = len(tdf)
    win_rate = (tdf["pnl_pct"] > 0).mean() * 100 if n_t else 0
    avg_win = tdf.loc[tdf["pnl_pct"] > 0, "pnl_pct"].mean() if (tdf["pnl_pct"] > 0).any() else 0
    avg_loss = tdf.loc[tdf["pnl_pct"] < 0, "pnl_pct"].mean() if (tdf["pnl_pct"] < 0).any() else 0
    avg_days = tdf["days"].mean() if n_t else 0

    fig.add_trace(
        go.Table(
            header=dict(values=["指标", "策略", "沪深300"]),
            cells=dict(values=[
                ["年化收益 %", "夏普", "最大回撤 %", "交易笔数", "胜率 %", "平均盈利 %", "平均亏损 %", "平均持仓天数"],
                [f"{sm.get('annual_return',0):.1f}", f"{sm.get('sharpe',0):.2f}",
                 f"{sm.get('max_drawdown',0):.1f}", f"{n_t}", f"{win_rate:.1f}",
                 f"{avg_win:.1f}", f"{avg_loss:.1f}", f"{avg_days:.0f}"],
                [f"{bm.get('annual_return',0):.1f}", f"{bm.get('sharpe',0):.2f}",
                 f"{bm.get('max_drawdown',0):.1f}", "-", "-", "-", "-", "-"],
            ]),
        ),
        row=3, col=2,
    )

    fig.update_layout(
        title_text=f"威科夫吸筹突破策略回测 ({timestamp})<br><sup>"
                   f"跌≥{int(DECLINE*100)}%+横盘 | 放量下跌→缩量反弹→缩量测试→放量突破 | "
                   f"止损=测试低点×{STOP_LOW} 或-{int((1-MAX_LOSS)*100)}% | 最多{MAX_HOLD}只×{int(POS_WEIGHT*100)}%</sup>",
        height=1000,
    )
    fig.update_yaxes(title_text="净值", row=1, col=1)
    fig.update_yaxes(title_text="年度收益 (%)", row=2, col=1)
    fig.update_yaxes(title_text="单笔盈亏 (%)", row=3, col=1)

    return fig.to_html(include_plotlyjs="cdn", full_html=True)


# --- main ---

def main():
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    lg = bs.login()
    if lg.error_code != "0":
        print(f"baostock login failed: {lg.error_msg}", file=sys.stderr)
        sys.exit(1)

    print("[1/5] 获取沪深300+中证500成分股...")
    index_stocks = get_index_stocks()
    codes = list(index_stocks.keys())
    print(f"      共 {len(codes)} 只")

    print("[2/5] 排除行业/ST...")
    all_names = get_stock_names()
    ind_map = get_industry_map()
    for code in list(codes):
        name = all_names.get(code, "")
        ind = ind_map.get(code, "")
        if ind in EXCLUDE_CSRC or "ST" in name:
            codes.remove(code)
    print(f"      剩余 {len(codes)} 只")

    print(f"[3/5] 获取OHLCV数据 ({len(codes)} 只, 8线程)...")
    all_data = fetch_all_stocks(codes)
    print(f"      有效数据: {len(all_data)} 只")

    cutoff = pd.Timestamp(DATA_MIN)
    dfs = {}
    for code, data in all_data.items():
        df = pd.DataFrame({k: data[k] for k in ["date", "open", "high", "low", "close", "volume"]})
        df["date"] = pd.to_datetime(df["date"])
        df = df.set_index("date")
        if df.index[0] <= cutoff:
            dfs[code] = df
    print(f"      满足历史长度: {len(dfs)} 只")

    print("[4/5] 回测...")
    bt_dates = pd.DatetimeIndex([])  # run_backtest 会用股票交易日并集
    strategy_values, trades = run_backtest(dfs, None, bt_dates)

    print("[5/5] 基准沪深300...")
    hs300_data = fetch_benchmark(HS300_CODE, "hs300")
    hs300_s = pd.Series(hs300_data["close"], index=pd.to_datetime(hs300_data["dates"])).sort_index()
    hs300_s = hs300_s.reindex(strategy_values.index).ffill()

    common = strategy_values.dropna().index.intersection(hs300_s.dropna().index)
    sv = strategy_values.reindex(common).ffill()
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
    print(f"      交易笔数: {n_t}  胜率: {win_rate:.1f}%")
    print(f"      总收益: {sm['total_return']:.1f}%")

    csv_file = f"wyckoff_result_{timestamp}.csv"
    pd.DataFrame({"date": sv.index, "strategy": sv.values, "hs300": bv.values}).to_csv(
        csv_file, index=False, encoding="utf-8-sig")
    print(f"      CSV -> {csv_file}")

    if n_t:
        tdf.sort_values("exit_date").to_csv(f"wyckoff_trades_{timestamp}.csv",
                                            index=False, encoding="utf-8-sig")
        print(f"      交易明细 -> wyckoff_trades_{timestamp}.csv")

    html_file = f"wyckoff_result_{timestamp}.html"
    html = plot_results(sv, bv, trades, timestamp)
    with open(html_file, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"      HTML -> {html_file}")

    bs.logout()
    print("\n完成.")


if __name__ == "__main__":
    main()
