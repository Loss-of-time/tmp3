#!/usr/bin/env python3
"""Wyckoff single-parameter sweep: vary one signal param at a time.
Varies one parameter while keeping others at defaults, reports annual
return / sharpe / drawdown / win-rate per value. Output CSV + HTML.
"""

import sys
import time
from datetime import datetime

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

import baostock as bs
import wyckoff as w
from backtest import calc_metrics

DEFAULTS = {
    "DECLINE": 0.30,
    "RANGE_MAX_WIDE": 0.30,
    "DUMP_RET": -0.02,
    "DUMP_VOL": 1.5,
    "BOUNCE_VOL": 0.8,
    "TEST_VOL": 0.6,
    "BREAK_VOL": 1.5,
    "BREAK_LOOKBACK": 60,
    "TEST_LOOKBACK": 30,
    "BOUNCE_LOOKBACK": 10,
    "BREAK_LOOKBACK_WIN": 20,
    "STOP_LOW": 0.95,
    "MAX_LOSS": 0.92,
    "DIST_VOL": 2.0,
    "DIST_RET": 0.01,
    "HUGE_VOL": 4.0,
    "MAX_HOLD": 5,
    "POS_WEIGHT": 0.20,
}

GRID = {
    "DECLINE": [0.25, 0.30, 0.35, 0.40, 0.50],
    "RANGE_MAX_WIDE": [0.20, 0.25, 0.30, 0.35],
    "DUMP_VOL": [1.2, 1.5, 1.8, 2.0],
    "TEST_VOL": [0.4, 0.5, 0.6, 0.8, 1.0],
    "BREAK_VOL": [1.2, 1.5, 1.8, 2.2],
    "DIST_VOL": [1.5, 2.0, 2.5, 3.0],
    "HUGE_VOL": [3.0, 4.0, 5.0, 6.0],
    "STOP_LOW": [0.93, 0.95, 0.97],
    "MAX_LOSS": [0.90, 0.92, 0.95],
}


def set_params(**kw):
    for k, v in DEFAULTS.items():
        setattr(w, k, v)
    for k, v in kw.items():
        setattr(w, k, v)


def load_data():
    lg = bs.login()
    if lg.error_code != "0":
        print(f"login failed: {lg.error_msg}", file=sys.stderr)
        sys.exit(1)
    print("[1/4] 成分股/行业/名称...", flush=True)
    index_stocks = w.get_index_stocks()
    codes = list(index_stocks.keys())
    all_names = w.get_stock_names()
    ind_map = w.get_industry_map()
    for code in list(codes):
        name = all_names.get(code, "")
        ind = ind_map.get(code, "")
        if ind in w.EXCLUDE_CSRC or "ST" in name:
            codes.remove(code)
    print(f"      {len(codes)} 只", flush=True)
    print("[2/4] OHLCV (缓存)...", flush=True)
    all_data = w.fetch_all_stocks(codes)
    cutoff = pd.Timestamp(w.DATA_MIN)
    dfs = {}
    for code, data in all_data.items():
        df = pd.DataFrame({k: data[k] for k in ["date", "open", "high", "low", "close", "volume"]})
        df["date"] = pd.to_datetime(df["date"])
        df = df.set_index("date")
        if df.index[0] <= cutoff:
            dfs[code] = df
    print(f"      {len(dfs)} 只有效", flush=True)
    print("[3/4] 基准沪深300...", flush=True)
    hs300_data = w.fetch_benchmark(w.HS300_CODE, "hs300")
    hs300_s = pd.Series(hs300_data["close"], index=pd.to_datetime(hs300_data["dates"])).sort_index()
    print("[4/4] 数据就绪", flush=True)
    return dfs, hs300_s


def run_one(dfs):
    t0 = time.time()
    values, trades = w.run_backtest(dfs, None, pd.DatetimeIndex([]))
    sv = values.dropna()
    sm = calc_metrics(sv)
    tdf = pd.DataFrame(trades)
    n_t = len(tdf)
    win_rate = (tdf["pnl_pct"] > 0).mean() * 100 if n_t else 0
    return sv, sm, n_t, win_rate, time.time() - t0


def main():
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    dfs, hs300_s = load_data()

    hs300_ann = None
    results = []
    rows = []

    set_params()
    sv0, sm0, n0, wr0, dt0 = run_one(dfs)
    hs300_ann = calc_metrics(hs300_s.reindex(sv0.index).ffill())["annual_return"]
    print(f"\n基线  年化:{sm0['annual_return']:6.1f}%  夏普:{sm0['sharpe']:.2f}  "
          f"回撤:{sm0['max_drawdown']:5.1f}%  胜率:{wr0:.1f}%  ({dt0:.0f}s)", flush=True)
    results.append({"param": "baseline", "value": "", "annual_return": sm0["annual_return"],
                    "sharpe": sm0["sharpe"], "max_drawdown": sm0["max_drawdown"],
                    "total_return": sm0["total_return"], "n_trades": n0, "win_rate": wr0})

    total = sum(len(v) for v in GRID.values())
    done = 0
    for param, values in GRID.items():
        for val in values:
            done += 1
            set_params(**{param: val})
            sv, sm, n_t, wr, dt = run_one(dfs)
            print(f"[{done}/{total}] {param}={val:<5}  年化:{sm['annual_return']:6.1f}%  "
                  f"夏普:{sm['sharpe']:.2f}  回撤:{sm['max_drawdown']:5.1f}%  "
                  f"胜率:{wr:4.1f}%  笔数:{n_t:3d}  ({dt:.0f}s)", flush=True)
            results.append({"param": param, "value": val, "annual_return": sm["annual_return"],
                            "sharpe": sm["sharpe"], "max_drawdown": sm["max_drawdown"],
                            "total_return": sm["total_return"], "n_trades": n_t, "win_rate": wr})
        set_params()

    df = pd.DataFrame(results)
    df["value_str"] = df["value"].astype(str)
    df["vs_benchmark"] = df["annual_return"] - hs300_ann

    csv_file = f"wyckoff_sweep_{timestamp}.csv"
    df.to_csv(csv_file, index=False, encoding="utf-8-sig")
    print(f"\nCSV -> {csv_file}")

    # HTML: one row per param, cols = 年化/夏普/回撤/胜率
    params = list(GRID.keys())
    fig = make_subplots(
        rows=len(params), cols=1,
        subplot_titles=[f"{p} (基线={getattr(w, p)})" for p in params],
        shared_xaxes=False,
        vertical_spacing=0.02,
    )
    for i, p in enumerate(params, 1):
        sub = df[df["param"] == p]
        x = sub["value"].astype(float)
        fig.add_trace(go.Scatter(x=x, y=sub["annual_return"], mode="lines+markers",
                                 name=f"{p} 年化", line=dict(color="steelblue"), showlegend=i == 1),
                      row=i, col=1)
        fig.add_trace(go.Scatter(x=x, y=sub["win_rate"], mode="lines+markers",
                                 name=f"{p} 胜率", line=dict(color="green"), showlegend=i == 1),
                      row=i, col=1)
        fig.add_hline(y=hs300_ann, line_dash="dot", line_color="coral", row=i, col=1)
    fig.update_layout(title=f"Wyckoff 单参数扫描 | 基准年化 {hs300_ann:.1f}% | {timestamp}", height=300 * len(params))
    html_file = f"wyckoff_sweep_{timestamp}.html"
    fig.write_html(html_file)
    print(f"HTML -> {html_file}")

    bs.logout()
    print("\n完成.")


if __name__ == "__main__":
    main()
