#!/usr/bin/env python3
"""Wyckoff joint-grid sweep. Varies a grid of params, others pinned.
Usage: pick grid combos, output CSV of all runs.
"""
import time
from datetime import datetime
import itertools
import pandas as pd
import baostock as bs
import wyckoff as w
from backtest import calc_metrics

BASE = dict(TEST_VOL=1.0, DECLINE=0.35, RANGE_MAX_WIDE=0.20, DUMP_VOL=1.8,
            BREAK_VOL=2.2, DIST_VOL=1.5, STOP_LOW=0.95, MAX_LOSS=0.92,
            HUGE_VOL=4.0, MAX_HOLD=5, POS_WEIGHT=0.20)

# grids: each is list of (param, [values]). Full cross product.
GRIDS = [
    {"TEST_VOL": [0.8, 1.0, 1.2], "BREAK_VOL": [1.5, 2.2]},
    {"DECLINE": [0.30, 0.35, 0.40], "RANGE_MAX_WIDE": [0.20, 0.30]},
    {"DUMP_VOL": [1.5, 1.8, 2.0], "BREAK_VOL": [1.8, 2.2]},
    {"TEST_VOL": [1.0, 1.2], "DUMP_VOL": [1.8, 2.0], "DIST_VOL": [1.5, 2.0]},
]

def load():
    bs.login()
    index_stocks = w.get_index_stocks()
    codes = list(index_stocks.keys())
    all_names = w.get_stock_names()
    ind_map = w.get_industry_map()
    for code in list(codes):
        if ind_map.get(code, "") in w.EXCLUDE_CSRC or "ST" in all_names.get(code, ""):
            codes.remove(code)
    all_data = w.fetch_all_stocks(codes)
    dfs = {}
    for code, data in all_data.items():
        df = pd.DataFrame({k: data[k] for k in ["date", "open", "high", "low", "close", "volume"]})
        df["date"] = pd.to_datetime(df["date"])
        df = df.set_index("date")
        if df.index[0] <= pd.Timestamp(w.DATA_MIN):
            dfs[code] = df
    return dfs

def run_one(dfs, overrides):
    for k, v in BASE.items():
        setattr(w, k, v)
    for k, v in overrides.items():
        setattr(w, k, v)
    t0 = time.time()
    sv, trades = w.run_backtest(dfs, None, pd.DatetimeIndex([]))
    sm = calc_metrics(sv.dropna())
    tdf = pd.DataFrame(trades)
    n = len(tdf)
    wr = (tdf["pnl_pct"] > 0).mean() * 100 if n else 0
    row = dict(overrides)
    row.update(annual=sm["annual_return"], sharpe=sm["sharpe"],
               mdd=sm["max_drawdown"], total=sm["total_return"],
               n_trades=n, win_rate=wr, secs=time.time() - t0)
    print(f"{row}", flush=True)
    return row

def main():
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    dfs = load()
    rows = []
    for grid in GRIDS:
        params = list(grid.keys())
        combos = list(itertools.product(*[grid[p] for p in params]))
        for combo in combos:
            ov = dict(zip(params, combo))
            rows.append(run_one(dfs, ov))
    df = pd.DataFrame(rows)
    df.to_csv(f"wyckoff_joint_{ts}.csv", index=False, encoding="utf-8-sig")
    print(f"\nCSV -> wyckoff_joint_{ts}.csv")
    bs.logout()

if __name__ == "__main__":
    main()
