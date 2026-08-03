#!/usr/bin/env python3
"""PE percentile-based backtest for A-share stocks.
10yr PE percentile: buy < 30%, sell > 60%, monthly rebalance, max 5 holdings.
"""

import json
import os
import sys
import time
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

# ponytail: baostock uses removed pandas3 DataFrame.append
pd.DataFrame.append = lambda self, other, ignore_index=False, sort=False: pd.concat(
    [self, other], ignore_index=ignore_index, sort=sort
)

import akshare as ak
import baostock as bs

# --- config ---
BT_CACHE = "cache_bt"
STOCK_CACHE = os.path.join(BT_CACHE, "stocks")

INDICES = {"000300": "沪深300", "000905": "中证500"}
EXCLUDE_CSRC = {
    "J66货币金融服务",
    "B06煤炭开采和洗选业",
    "B07石油和天然气开采业",
    "C25石油、煤炭及其他燃料加工业",
    "B09有色金属矿采选业",
    "C32有色金属冶炼和压延加工业",
    "K70房地产业",
}

PE_BUY = 30
PE_SELL = 60
MAX_HOLD = 5
LOOKBACK = 10
MIN_DATA_POINTS = 100

START = "2020-01-01"
END = "2026-07-31"

COMMISSION = 0.0003
STAMP_TAX = 0.0005

HS300_CODE = "sh.000300"
ETF_CODE = "sh.510300"

os.makedirs(STOCK_CACHE, exist_ok=True)


def akshare_to_baostock(code):
    if code.startswith(("0", "3")):
        return f"sz.{code}"
    if code.startswith("6"):
        return f"sh.{code}"
    if code.startswith(("4", "8")):
        return f"bj.{code}"
    return f"sh.{code}"


# --- data fetching ---

def fetch_one_stock(code):
    cache_file = os.path.join(STOCK_CACHE, f"{code}.json")
    if os.path.exists(cache_file):
        with open(cache_file) as f:
            return json.load(f)

    bs_code = akshare_to_baostock(code)
    try:
        rs = bs.query_history_k_data_plus(
            bs_code, "date,close,peTTM",
            start_date="2010-01-01", end_date=END,
            frequency="d", adjustflag="3",
        )
        raw = rs.get_data()
    except Exception:
        return None

    if raw.empty:
        return None

    raw["close"] = pd.to_numeric(raw["close"], errors="coerce")
    raw["peTTM"] = pd.to_numeric(raw["peTTM"], errors="coerce")
    raw = raw.dropna(subset=["close"])
    raw = raw.dropna(subset=["peTTM"])

    if raw.empty:
        return None

    result = {
        "code": code,
        "dates": raw["date"].tolist(),
        "close": [round(float(x), 2) for x in raw["close"]],
        "pe": [round(float(x), 4) for x in raw["peTTM"]],
    }

    with open(cache_file, "w") as f:
        json.dump(result, f, ensure_ascii=False)

    return result


def fetch_all_stocks(codes):
    results = {}
    total = len(codes)
    t0 = time.time()
    for i, code in enumerate(codes, 1):
        r = fetch_one_stock(code)
        if r:
            results[code] = r
        if i % 50 == 0 or i == total:
            elapsed = time.time() - t0
            eta = elapsed / i * (total - i)
            print(f"      {i}/{total} ({i/total*100:.0f}%) 耗时:{elapsed:.0f}s 预计剩余:{eta:.0f}s")
    return results


def fetch_benchmark(bs_code, label):
    cache_file = os.path.join(BT_CACHE, f"{label}.json")
    if os.path.exists(cache_file):
        with open(cache_file) as f:
            return json.load(f)

    try:
        rs = bs.query_history_k_data_plus(
            bs_code, "date,close",
            start_date="2010-01-01", end_date=END,
            frequency="d", adjustflag="3",
        )
        raw = rs.get_data()
    except Exception:
        return None

    if raw.empty:
        return None

    raw["close"] = pd.to_numeric(raw["close"], errors="coerce")
    raw = raw.dropna(subset=["close"])

    result = {
        "dates": raw["date"].tolist(),
        "close": [round(float(x), 2) for x in raw["close"]],
    }

    with open(cache_file, "w") as f:
        json.dump(result, f, ensure_ascii=False)

    return result


def get_index_stocks():
    stocks = {}
    for idx_code, idx_name in INDICES.items():
        try:
            df = ak.index_stock_cons_csindex(symbol=idx_code)
            for code in df["成分券代码"].astype(str).str.zfill(6):
                stocks[code] = idx_name
        except Exception as e:
            print(f"  warn: {idx_name} fetch failed: {e}", file=sys.stderr)
    return stocks


def get_industry_map():
    try:
        rs = bs.query_stock_industry()
        df = rs.get_data()
        df["ak_code"] = df["code"].str.replace("sh.", "").str.replace("sz.", "").str.replace("bj.", "")
        return dict(zip(df["ak_code"], df["industry"]))
    except Exception as e:
        print(f"  warn: industry fetch failed: {e}", file=sys.stderr)
        return {}


def get_stock_names():
    try:
        rs = bs.query_stock_basic()
        df = rs.get_data()
        df = df[df["type"] == "1"]
        df["ak_code"] = df["code"].str.extract(r"\.(\d{6})$")[0]
        return dict(zip(df["ak_code"], df["code_name"]))
    except Exception:
        return {}


# --- signal generation ---

def pe_percentile_at(pe_series, date):
    """PE 10yr percentile at date. Returns NaN if insufficient data."""
    cutoff = date - pd.DateOffset(years=LOOKBACK)
    hist = pe_series[cutoff:date]
    hist = hist.dropna()
    if len(hist) < MIN_DATA_POINTS:
        return np.nan
    current = hist.iloc[-1]
    if current <= 0:
        return np.nan
    return (hist <= current).mean() * 100


def compute_signals(pe_df, month_ends):
    """Pre-compute PE percentiles for all stocks at each month-end."""
    codes = pe_df.columns.tolist()
    signals = pd.DataFrame(index=month_ends, columns=codes, dtype=float)

    for i, me in enumerate(month_ends):
        for code in codes:
            signals.loc[me, code] = pe_percentile_at(pe_df[code], me)
        if (i + 1) % 10 == 0:
            print(f"      signal {i+1}/{len(month_ends)}")

    return signals


# --- backtest ---

def target_weights(codes, pcts, held, buy_pct=PE_BUY, sell_pct=PE_SELL):
    """Determine target weights given PE percentiles and current holdings."""
    qualifying = []
    for code in codes:
        p = pcts.get(code, np.nan)
        if not np.isnan(p) and p < buy_pct:
            qualifying.append((code, p))

    qualifying.sort(key=lambda x: x[1])

    force_sell = set()
    for code in held:
        p = pcts.get(code, np.nan)
        if not np.isnan(p) and p > sell_pct:
            force_sell.add(code)

    # Remove force-sell from qualifying
    qualifying = [(c, p) for c, p in qualifying if c not in force_sell]

    # Build target: top MAX_HOLD by PE percentile
    selected = set()
    for code, _ in qualifying[:MAX_HOLD]:
        selected.add(code)

    weights = {}
    n = len(selected)
    if n > 0:
        w = 1.0 / MAX_HOLD
        for code in selected:
            weights[code] = w

    return weights, force_sell, selected


def run_backtest(close_df, pe_df, signals, month_ends, etf_close, etf_dates,
                 buy_pct=PE_BUY, sell_pct=PE_SELL):
    """Run monthly rebalancing backtest. Returns daily portfolio value and trade log."""
    all_dates = close_df.index
    codes = close_df.columns.tolist()

    # ETF returns (fallback to 2% annual cash if ETF data insufficient)
    if len(etf_close) > 200:
        etf_s = pd.Series(etf_close, index=etf_dates).sort_index()
        etf_returns = etf_s.pct_change().reindex(all_dates).fillna(0)
    else:
        # 2% annual cash return
        etf_returns = pd.Series(0.02 / 252, index=all_dates)

    # Daily returns for all stocks
    stock_returns = close_df.pct_change().fillna(0)

    # Portfolio state
    positions = np.zeros(len(codes))  # dollar amounts per stock
    etf_position = 1.0  # dollar amount in ETF
    capital = 1.0

    portfolio_values = pd.Series(index=all_dates, dtype=float)
    trade_log = []

    signals_dict = {}
    for me in month_ends:
        signals_dict[me] = signals.loc[me].to_dict()

    for i, date in enumerate(all_dates):
        if i == 0:
            portfolio_values.iloc[i] = capital
            continue

        r_today = stock_returns.iloc[i].values
        etf_r = etf_returns.iloc[i]

        # PnL
        stock_pnl = np.dot(positions, r_today)
        etf_pnl = etf_position * etf_r
        pnl = stock_pnl + etf_pnl
        capital += pnl

        # Drift positions
        positions *= (1 + r_today)
        etf_position *= (1 + etf_r)

        # Rebalance
        if date in month_ends and date in signals_dict:
            pcts = signals_dict[date]
            held = {codes[j] for j in range(len(codes)) if positions[j] > 0}
            tw, force_sell, selected = target_weights(codes, pcts, held, buy_pct, sell_pct)

            # Pre-cost target positions
            target_pos = np.zeros(len(codes))
            for code, w in tw.items():
                j = codes.index(code)
                target_pos[j] = w * capital
            target_etf = capital * (1 - len(tw) / MAX_HOLD)

            # Trading cost
            sells = max(0, (positions - target_pos).sum())
            buys = max(0, (target_pos - positions).sum())
            trade_cost = sells * (COMMISSION + STAMP_TAX) + buys * COMMISSION
            capital -= trade_cost

            # Re-normalize after cost
            scale = capital / (target_pos.sum() + target_etf) if (target_pos.sum() + target_etf) > 0 else 1
            positions = target_pos * scale
            etf_position = target_etf * scale

            trade_log.append({
                "date": date,
                "held": sorted(selected),
                "weights": {c: round(positions[codes.index(c)] / capital, 4) for c in selected if positions[codes.index(c)] > 1e-10},
                "etf_weight": round(etf_position / capital, 4),
                "cost": round(trade_cost, 6),
            })

        portfolio_values.iloc[i] = capital

    return portfolio_values, trade_log


# --- metrics ---

def calc_metrics(values, rf_annual=0.025):
    daily_returns = values.pct_change().dropna()
    if len(daily_returns) < 2:
        return {}
    years = len(daily_returns) / 252
    total_return = values.iloc[-1] / values.iloc[0] - 1
    annual_return = (1 + total_return) ** (1 / years) - 1
    excess = daily_returns - rf_annual / 252
    sharpe = float(excess.mean() / excess.std() * np.sqrt(252)) if excess.std() > 0 else 0
    peak = values.cummax()
    drawdown = (peak - values) / peak
    max_dd = float(drawdown.max())
    return {
        "total_return": round(float(total_return) * 100, 2),
        "annual_return": round(float(annual_return) * 100, 2),
        "sharpe": round(float(sharpe), 2),
        "max_drawdown": round(float(max_dd) * 100, 2),
        "years": round(years, 1),
    }


# --- plot ---

def plot_results(
    strategy_values, hs300_values, trade_log, timestamp,
    buy_pct=PE_BUY, sell_pct=PE_SELL,
):
    fig = make_subplots(
        rows=2, cols=2,
        subplot_titles=("净值曲线", "年度收益对比", "分位信号热力图概览", "策略指标"),
        row_heights=[0.6, 0.4],
        specs=[[{"colspan": 2}, None], [{}, {"type": "table"}]],
    )

    # Normalize both to start at 1
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

    # Trade markers
    for t in trade_log:
        if t["date"] in sv.index:
            fig.add_trace(
                go.Scatter(
                    x=[t["date"]], y=[sv.loc[t["date"]]],
                    mode="markers", marker=dict(color="green", size=5, symbol="triangle-up"),
                    showlegend=False, hoverinfo="skip",
                ),
                row=1, col=1,
            )

    # Annual returns bar chart
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

    # Metrics table
    sm = calc_metrics(strategy_values)
    bm = calc_metrics(hs300_values)
    fig.add_trace(
        go.Table(
            header=dict(values=["指标", "策略", "沪深300"]),
            cells=dict(values=[
                ["年化收益 %", "夏普比率", "最大回撤 %"],
                [f"{sm.get('annual_return',0):.1f}", f"{sm.get('sharpe',0):.2f}", f"{sm.get('max_drawdown',0):.1f}"],
                [f"{bm.get('annual_return',0):.1f}", f"{bm.get('sharpe',0):.2f}", f"{bm.get('max_drawdown',0):.1f}"],
            ]),
        ),
        row=2, col=2,
    )

    fig.update_layout(
        title_text=(
            f"PE 10年分位策略回测 ({timestamp})<br><sup>"
            f"买&lt;{buy_pct}% 卖&gt;{sell_pct}% | 月频 | 最多{MAX_HOLD}只 | "
            f"沪深300+中证500 | 2020-2026</sup>"
        ),
        height=800,
    )
    fig.update_yaxes(title_text="净值", row=1, col=1)
    fig.update_yaxes(title_text="年度收益 (%)", row=2, col=1)

    return fig.to_html(include_plotlyjs="cdn", full_html=True)


# --- main ---

# --- main / data prep ---


def prepare_backtest_data(print_progress=True):
    """Load cached data, build DataFrames, compute signals, fetch benchmarks.
    Returns (close_df, pe_df, signals, month_ends, etf_close, etf_dates_pd, hs300_data).
    """
    if print_progress:
        print("[1/5] 获取沪深300+中证500成分股...")
    index_stocks = get_index_stocks()
    codes = list(index_stocks.keys())
    if print_progress:
        print(f"      共 {len(codes)} 只")

    if print_progress:
        print("[2/5] 获取行业及名称...")
    all_names = get_stock_names()
    ind_map = get_industry_map()

    excluded_industry = set()
    excluded_st = set()
    for code in list(codes):
        name = all_names.get(code, "")
        ind = ind_map.get(code, "")
        if ind in EXCLUDE_CSRC:
            excluded_industry.add(code)
            codes.remove(code)
        elif "ST" in name:
            excluded_st.add(code)
            codes.remove(code)
    if print_progress:
        print(f"      排除行业 {len(excluded_industry)} 只, 排除ST {len(excluded_st)} 只, 剩余 {len(codes)} 只")

    if print_progress:
        print(f"[3/5] 获取PE/价格日频数据 ({len(codes)} 只, 串行)...")
    all_data = fetch_all_stocks(codes)
    if print_progress:
        print(f"      有效数据: {len(all_data)} 只")

    cutoff_date = pd.Timestamp(START) - pd.DateOffset(years=LOOKBACK) + pd.DateOffset(months=2)
    valid_codes = []
    for code, data in all_data.items():
        first_date = pd.Timestamp(data["dates"][0])
        if first_date <= cutoff_date:
            valid_codes.append(code)
    excluded_short = set(all_data.keys()) - set(valid_codes)
    if print_progress:
        print(f"      排除不足10年: {len(excluded_short)} 只, 剩余 {len(valid_codes)} 只")

    if len(valid_codes) < 3:
        raise ValueError("股票太少，无法回测.")

    if print_progress:
        print("[4/5] 构建收益/PE矩阵 + 计算月度信号...")

    close_dfs = {}
    pe_dfs = {}
    for code in valid_codes:
        data = all_data[code]
        s_close = pd.Series(data["close"], index=pd.to_datetime(data["dates"]), name=code)
        s_pe = pd.Series(data["pe"], index=pd.to_datetime(data["dates"]), name=code)
        close_dfs[code] = s_close
        pe_dfs[code] = s_pe

    close_df = pd.DataFrame(close_dfs).sort_index()
    pe_df = pd.DataFrame(pe_dfs).sort_index()
    close_df = close_df[START:END]
    close_df = close_df.ffill()
    pe_df = pe_df.ffill()

    month_ends = close_df.groupby(close_df.index.to_period("M")).apply(lambda g: g.index[-1])
    month_ends = pd.DatetimeIndex(month_ends)

    signals = compute_signals(pe_df, month_ends)
    if print_progress:
        print(f"      信号计算完成, {len(month_ends)} 个月度, 股票{signals.shape[1]}只")

    if print_progress:
        print("[5/5] 获取沪深300指数 & 510300 ETF数据...")
    hs300_data = fetch_benchmark(HS300_CODE, "hs300")
    etf_data = fetch_benchmark(ETF_CODE, "etf510300")

    etf_close = []
    etf_dates_pd = pd.DatetimeIndex([])
    if etf_data and len(etf_data["close"]) > 0:
        etf_close = etf_data["close"]
        etf_dates_pd = pd.to_datetime(etf_data["dates"])

    if not hs300_data or len(hs300_data["close"]) < 2:
        raise ValueError("沪深300数据不可用.")

    return close_df, pe_df, signals, month_ends, etf_close, etf_dates_pd, hs300_data


def main():
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    lg = bs.login()
    if lg.error_code != "0":
        print(f"baostock login failed: {lg.error_msg}", file=sys.stderr)
        sys.exit(1)

    close_df, pe_df, signals, month_ends, etf_close, etf_dates_pd, hs300_data = \
        prepare_backtest_data()

    print("[6/6] 回测中...")
    strategy_values, trade_log = run_backtest(
        close_df, pe_df, signals, month_ends, etf_close, etf_dates_pd,
    )

    # HS300 benchmark values
    hs300_s = pd.Series(hs300_data["close"], index=pd.to_datetime(hs300_data["dates"])).sort_index()
    hs300_s = hs300_s.reindex(close_df.index).ffill()
    hs300_values = hs300_s[START:END]

    common_idx = strategy_values.dropna().index.intersection(hs300_values.dropna().index)
    sv = strategy_values.reindex(common_idx).ffill()
    bv = hs300_values.reindex(common_idx).ffill()

    sm = calc_metrics(sv)
    bm = calc_metrics(bv)

    print("\n--- 回测结果 ---")
    print(f"      策略年化收益: {sm['annual_return']:.1f}%")
    print(f"      沪深300年化收益: {bm['annual_return']:.1f}%")
    print(f"      策略夏普: {sm['sharpe']:.2f}")
    print(f"      策略最大回撤: {sm['max_drawdown']:.1f}%")
    print(f"      调仓次数: {len(trade_log)}")
    if trade_log:
        print(f"      最后持仓 ({trade_log[-1]['date']}): {trade_log[-1]['held']}")

    csv_file = f"bt_result_{timestamp}.csv"
    df_out = pd.DataFrame({"date": sv.index, "strategy": sv.values, "hs300": bv.values})
    df_out.to_csv(csv_file, index=False, encoding="utf-8-sig")
    print(f"      CSV -> {csv_file}")

    html_file = f"bt_result_{timestamp}.html"
    html = plot_results(sv, bv, trade_log, timestamp, PE_BUY, PE_SELL)
    with open(html_file, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"      HTML -> {html_file}")

    bs.logout()
    print("\n完成.")


if __name__ == "__main__":
    main()
