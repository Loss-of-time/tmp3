#!/usr/bin/env python3
"""v7 持仓期间各 ETF 对收益的贡献分解。

重放 rotation_backtest 的完全相同逻辑, 额外记录每段持仓:
- 入场/离场日期与价格, 投入资金 (invested), 离场市值 (exit_value)
- 该段对总收益的贡献 = (exit_value - invested) / 1.0 (占初始净值1.0的比例, 即 %)
- 该 ETF 自身在持仓窗口的价格涨幅 (含底仓价格走势)

输出: 按 ETF 汇总表 + 每段明细。
"""

import json
import os
from datetime import datetime

import numpy as np
import pandas as pd

from backtest import COMMISSION
from etf_rot_signal import (
    MA_N, LOOKBACK, MOM_GAP, MIN_MOM, TRAIL, COOLDOWN, BASE_W, BASE_ETF,
    CASH_APR, BT_START, ETFS_DIR, load_data,
)


def contrib_backtest(close_df, open_df=None, bt_start=BT_START, ma_n=MA_N,
                     lookback=LOOKBACK, mom_gap=MOM_GAP, min_mom=MIN_MOM,
                     trail=TRAIL, base_w=BASE_W, base_etf=BASE_ETF,
                     cooldown=COOLDOWN):
    bt = close_df[bt_start:]
    sig_close = bt.shift(1)
    ma = sig_close.rolling(ma_n).mean()
    above = sig_close > ma
    mom = sig_close.pct_change(lookback)
    lowvol = bt[base_etf]
    fill = open_df[bt_start:] if open_df is not None else bt

    values = pd.Series(index=bt.index, dtype=float)
    base_shares = base_w / lowvol.iloc[0]
    base_entry_px = lowvol.iloc[0]
    rot_cash = 1.0 - base_w
    shares = 0.0
    code = None
    peak = 0.0
    last_sell = {}
    contribs = []
    cur_hold = None

    for i, date in enumerate(bt.index):
        if i == 0:
            values.iloc[i] = 1.0
            continue

        px = sig_close.loc[date, code] if code else None
        signal = above.loc[date]

        if code is not None:
            peak = max(peak, px)
            reason = None
            if not bool(signal[code]):
                reason = "trend"
            elif px <= peak * (1 - trail):
                reason = "trail"
            if reason:
                exec_px = fill.loc[date, code]
                proceeds = shares * exec_px * (1 - COMMISSION)
                rot_cash += proceeds
                last_sell[code] = i
                contribs.append({
                    "code": cur_hold["code"], "entry": cur_hold["entry"],
                    "exit": date, "invested": cur_hold["invested"],
                    "exit_value": proceeds,
                    "etf_ret": exec_px / cur_hold["entry_px"] - 1,
                    "reason": reason,
                })
                shares = 0.0
                code = None
                peak = 0.0
                cur_hold = None

        elig = [c for c in signal[signal].index
                if not np.isnan(mom.loc[date, c]) and mom.loc[date, c] > min_mom
                and (cooldown == 0 or i - last_sell.get(c, -10**9) > cooldown)]
        if elig:
            peak60 = sig_close.iloc[max(0, i-60):i+1].max(axis=0)
            elig = [c for c in elig if sig_close.loc[date, c] >= peak60[c] * (1 - trail)]
        if len(elig) > 0:
            best = mom.loc[date, elig].idxmax()
            best_px = fill.loc[date, best]
            if code is None:
                target = rot_cash
                shares = target / best_px
                cost = target * COMMISSION
                rot_cash -= target + cost
                code = best
                peak = best_px
                cur_hold = {"code": best, "entry": date, "entry_px": best_px,
                            "invested": target}
            elif best != code:
                cur_mom = mom.loc[date, code]
                best_mom = mom.loc[date, best]
                if best_mom - cur_mom > mom_gap:
                    old_code = code
                    exec_px = fill.loc[date, old_code]
                    proceeds = shares * exec_px * (1 - COMMISSION)
                    rot_cash += proceeds
                    contribs.append({
                        "code": cur_hold["code"], "entry": cur_hold["entry"],
                        "exit": date, "invested": cur_hold["invested"],
                        "exit_value": proceeds,
                        "etf_ret": exec_px / cur_hold["entry_px"] - 1,
                        "reason": f"换 {old_code}",
                    })
                    target = rot_cash
                    shares = target / best_px
                    cost = target * COMMISSION
                    rot_cash -= target + cost
                    code = best
                    peak = best_px
                    cur_hold = {"code": best, "entry": date, "entry_px": best_px,
                                "invested": target}

        px = bt.loc[date, code] if code else None
        mv = base_shares * lowvol.iloc[i] + rot_cash + (shares * px if code else 0)
        values.iloc[i] = mv

    if cur_hold is not None:
        last_px = bt.loc[bt.index[-1], cur_hold["code"]]
        contribs.append({
            "code": cur_hold["code"], "entry": cur_hold["entry"],
            "exit": bt.index[-1], "invested": cur_hold["invested"],
            "exit_value": shares * last_px * (1 - COMMISSION),
            "etf_ret": last_px / cur_hold["entry_px"] - 1,
            "reason": "持有中",
        })

    base_contrib = base_shares * (lowvol.iloc[-1] - base_entry_px)
    return values, contribs, base_contrib


def main():
    etfs, opens, _ = load_data()
    close_df = pd.DataFrame(etfs).sort_index().ffill()
    open_df = pd.DataFrame(opens).sort_index().reindex(close_df.index).ffill()
    names = {json.load(open(f))["code"]: json.load(open(f))["name"]
             for f in glob.glob(os.path.join(ETFS_DIR, "*.json"))}

    values, contribs, base_contrib = contrib_backtest(close_df, open_df=open_df)

    df = pd.DataFrame(contribs)
    df["days"] = (pd.to_datetime(df["exit"]) - pd.to_datetime(df["entry"])).dt.days
    df["contrib_pct"] = (df["exit_value"] - df["invested"]) / 1.0 * 100
    df["code"] = df["code"].map(lambda c: f"{names[c]} ({c})")

    total_ret = (values.iloc[-1] - 1) * 100
    print(f"策略总收益: {total_ret:+.1f}%")
    print(f"底仓 {names[BASE_ETF]}({BASE_ETF}) 恒持贡献: {base_contrib*100:+.1f}% "
          f"(占 {base_contrib/(values.iloc[-1]-1)*100:.0f}% 的总收益)")
    print(f"轮动仓总贡献(明细之和): {df['contrib_pct'].sum():+.1f}%")
    print()

    agg = df.groupby("code").agg(
        n=("code", "size"),
        total=("contrib_pct", "sum"),
        avg_days=("days", "mean"),
        etf_ret_avg=("etf_ret", lambda s: s.mean() * 100),
    ).sort_values("total", ascending=False)
    agg["avg_days"] = agg["avg_days"].round(0)
    agg["etf_ret_avg"] = agg["etf_ret_avg"].round(1)
    agg["total"] = agg["total"].round(2)
    print("=== 按 ETF 汇总 (轮动仓, 贡献占总收益%) ===")
    print(agg.to_string())

    print("\n=== 每段持仓明细 ===")
    detail = df[["code", "entry", "exit", "days", "invested", "exit_value",
                 "etf_ret", "contrib_pct", "reason"]].copy()
    detail["invested"] = detail["invested"].round(4)
    detail["exit_value"] = detail["exit_value"].round(4)
    detail["etf_ret"] = (detail["etf_ret"] * 100).round(1)
    print(detail.to_string(index=False))

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    df.to_csv(f"etf_rot_contrib_{ts}.csv", index=False, encoding="utf-8-sig")
    print(f"\nCSV -> etf_rot_contrib_{ts}.csv")


if __name__ == "__main__":
    import glob
    main()
