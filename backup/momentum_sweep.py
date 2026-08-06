#!/usr/bin/env python3
"""参数稳定性扫描 + 固定止损测试。

围绕 momentum.py 获胜配置逐参数扰动看稳定性, 并测试加固定止损(止跌)的效果。
核心回测逻辑与 momentum.py 保持一致, 增加 stop_loss 参数 (从买入价回撤即离场)。
"""
import sys

import numpy as np
import pandas as pd

import baostock as bs
import momentum as mo
import wyckoff as w

BASE = dict(lookback=40, skip=5, trail=0.35, ma_n=200, min_px=10, rebal=10,
            pos_weight=1.0, stop_loss=0.0)


def run(dfs, bt_dates, close, lookback, skip, trail, ma_n, min_px, rebal,
        pos_weight=1.0, stop_loss=0.0):
    mom = close.shift(skip).pct_change(lookback)
    ma = close.rolling(ma_n, min_periods=ma_n).mean()
    above_ma = close > ma
    eligible = ma.notna() & (close >= min_px)

    values = pd.Series(index=bt_dates, dtype=float)
    cash = 1.0
    pos = None
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

        if pos is not None:
            px = close.loc[date, pos["code"]]
            if not np.isnan(px):
                pos["peak"] = max(pos["peak"], px)
                trend_ok = above_ma.loc[date, pos["code"]]
                reason = None
                if stop_loss > 0 and px <= pos["entry_price"] * (1 - stop_loss):
                    reason = "stoploss"
                elif px <= pos["peak"] * (1 - trail):
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

        if is_rebal and pos is None and has_signal:
            px = close.loc[date, best]
            target_amt = cash * pos_weight
            shares = target_amt / px
            cost = target_amt * w.COMMISSION
            cash -= target_amt + cost
            pos = {"code": best, "shares": shares, "entry_price": px,
                   "entry_date": date, "peak": px}

        mv = cash
        if pos is not None:
            mv += pos["shares"] * close.loc[date, pos["code"]]
        cash *= (1 + mo.CASH_APR / 252)
        values.iloc[i] = mv

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


def summarize(values, trades):
    sm = mo.calc_metrics(values)
    tdf = pd.DataFrame(trades) if trades else pd.DataFrame(
        columns=["pnl_pct", "days"])
    n = len(tdf)
    wr = (tdf["pnl_pct"] > 0).mean() * 100 if n else 0
    return (f"年化{sm['annual_return']:6.1f}% 夏普{sm['sharpe']:5.2f} "
            f"回撤{sm['max_drawdown']:5.1f}% 总{sm['total_return']:8.1f}% "
            f"笔数{n:3d} 胜率{wr:5.1f}%")


def main():
    lg = bs.login()
    if lg.error_code != "0":
        print(f"baostock login failed: {lg.error_msg}", file=sys.stderr)
        sys.exit(1)

    dfs, _ = mo.load_data()
    bt_dates = pd.DatetimeIndex([])
    for df in dfs.values():
        bt_dates = bt_dates.union(df.index)
    bt_dates = bt_dates[(bt_dates >= pd.Timestamp(mo.BT_START))
                        & (bt_dates <= pd.Timestamp(w.END))].sort_values()
    close = pd.DataFrame({c: df["close"] for c, df in dfs.items()}).reindex(bt_dates).ffill()

    values, trades = run(dfs, bt_dates, close, **BASE)
    mo_values, mo_trades = mo.momentum_backtest(dfs)
    assert (values.round(10) == mo_values.round(10)).all(), "与 momentum.py 不一致!"
    print(f"基线 (与 momentum.py 一致): {summarize(values, trades)}")

    print("\n--- 参数稳定性扫描 (每次只动一个参数) ---")
    sweeps = {
        "lookback":  [20, 30, 50, 60, 120],
        "skip":      [1, 3, 10, 20],
        "trail":     [0.25, 0.30, 0.40, 0.50, 0.60],
        "ma_n":      [100, 150, 250, 300],
        "min_px":    [5, 8, 15, 20],
        "rebal":     [5, 20, 40],
    }
    for name, vals in sweeps.items():
        for v in vals:
            cfg = dict(BASE, **{name: v})
            vs, ts = run(dfs, bt_dates, close, **cfg)
            label = f"{v:g}"
            print(f"  {name}={label:<5s} -> {summarize(vs, ts)}")

    print("\n--- 固定止损(止跌)测试: 从买入价回撤 X 即离场 ---")
    for sl in [0.10, 0.15, 0.20, 0.25, 0.30]:
        cfg = dict(BASE, stop_loss=sl)
        vs, ts = run(dfs, bt_dates, close, **cfg)
        print(f"  stop_loss={sl:.2f} -> {summarize(vs, ts)}")
        for t in ts:
            if t["reason"] == "stoploss":
                print(f"      {t['code']} {t['entry_date']:%Y-%m-%d}->{t['exit_date']:%Y-%m-%d} "
                      f"{t['pnl_pct']:+.1f}%")
        print()

    bs.logout()


if __name__ == "__main__":
    main()
