#!/usr/bin/env python3
"""缓解"来回挨打"的实验: 熊市轮动仓禁止开新仓 vs 延长冷却 vs 最短持有期。

在 rotation_backtest 相同逻辑上加一个 regime 闸门 (hs300 收盘 > 自身 MA_R 才允许
开新仓/切换), 对比:
  0 baseline
  A 冷却 40/60 (同标的不买回)
  B 最短持有 min_hold 天 (买入后 N 天内不触发 trail/trend 卖出)
  C regime 闸门: 大盘破位禁止开新仓 (持仓仍按自身信号处理)
  D C + 冷却 60
"""

import pandas as pd
import numpy as np
from backtest import COMMISSION, calc_metrics
from etf_rot_signal import (MA_N, LOOKBACK, MOM_GAP, MIN_MOM, TRAIL, COOLDOWN,
                            BASE_W, BASE_ETF, CASH_APR, BT_START, load_data)


def run(close_df, open_df, hs300=None, ma_n=MA_N, lookback=LOOKBACK, mom_gap=MOM_GAP,
        min_mom=MIN_MOM, trail=TRAIL, base_w=BASE_W, base_etf=BASE_ETF,
        cooldown=COOLDOWN, min_hold=0, regime=None, conf=0, entry_pause=0):
    bt = close_df[BT_START:]
    sig_close = bt.shift(1)
    ma = sig_close.rolling(ma_n).mean()
    above = sig_close > ma
    above_win = above.rolling(conf).min() if conf > 0 else None  # 连续 conf 日站上 MA
    mom = sig_close.pct_change(lookback)
    lowvol = bt[base_etf]
    fill = open_df[BT_START:]

    values = pd.Series(index=bt.index, dtype=float)
    base_shares = base_w / lowvol.iloc[0]
    rot_cash = 1.0 - base_w
    shares = 0.0
    code = None
    peak = 0.0
    last_sell = {}
    entry_i = None
    last_trend_exit = -10**9

    for i, date in enumerate(bt.index):
        if i == 0:
            values.iloc[i] = 1.0
            continue
        px = sig_close.loc[date, code] if code else None
        signal = above.loc[date]
        gate = True if regime is None else bool(regime.loc[date])

        if code is not None:
            peak = max(peak, px)
            reason = None
            if not bool(signal[code]):
                reason = "trend"
            elif px <= peak * (1 - trail):
                reason = "trail"
            if reason and (min_hold == 0 or i - entry_i >= min_hold):
                exec_px = fill.loc[date, code]
                rot_cash += shares * exec_px * (1 - COMMISSION)
                last_sell[code] = i
                if reason == "trend":
                    last_trend_exit = i
                shares = 0.0
                code = None
                peak = 0.0
                entry_i = None

        elig = [c for c in signal[signal].index
                if not np.isnan(mom.loc[date, c]) and mom.loc[date, c] > min_mom
                and (cooldown == 0 or i - last_sell.get(c, -10**9) > cooldown)]
        if conf > 0:
            elig = [c for c in elig if above_win.loc[date, c] == 1]
        if gate and i - last_trend_exit >= entry_pause and len(elig) > 0:
            peak60 = sig_close.iloc[max(0, i-60):i+1].max(axis=0)
            elig = [c for c in elig if sig_close.loc[date, c] >= peak60[c] * (1 - trail)]
        if gate and i - last_trend_exit >= entry_pause and len(elig) > 0:
            best = mom.loc[date, elig].idxmax()
            best_px = fill.loc[date, best]
            if code is None:
                target = rot_cash
                shares = target / best_px
                rot_cash -= target + target * COMMISSION
                code = best
                peak = best_px
                entry_i = i
            elif best != code:
                cur_mom = mom.loc[date, code]
                best_mom = mom.loc[date, best]
                if best_mom - cur_mom > mom_gap:
                    exec_px = fill.loc[date, code]
                    rot_cash += shares * exec_px * (1 - COMMISSION)
                    shares = 0.0
                    target = rot_cash
                    shares = target / best_px
                    rot_cash -= target + target * COMMISSION
                    code = best
                    peak = best_px
                    entry_i = i

        px = bt.loc[date, code] if code else None
        mv = base_shares * lowvol.iloc[i] + rot_cash + (shares * px if code else 0)
        values.iloc[i] = mv
    return values


etfs, opens, hs = load_data()
close_df = pd.DataFrame(etfs).sort_index().ffill()
open_df = pd.DataFrame(opens).sort_index().reindex(close_df.index).ffill()
# 大盘牛熊闸门: 用昨日收盘 vs 其 MA200 判定 (与 sig_close 逻辑一致, 无前视)
hs300 = hs.reindex(close_df.index).ffill()
regime = (hs300.shift(1) > hs300.shift(1).rolling(MA_N).mean())

def stats(name, sv, trades_note=""):
    m = calc_metrics(sv)
    print(f"{name:<22} 年化{m['annual_return']:5.1f}% 夏普{m['sharpe']:4.2f} 回撤{m['max_drawdown']:5.1f}% 总{m['total_return']:5.1f}% {trades_note}")

base = run(close_df, open_df)
stats("0 baseline", base)

# A: cooldown
for cd in (40, 60):
    stats(f"A 冷却{cd}", run(close_df, open_df, cooldown=cd))

# B: min_hold
for mh in (10, 20, 30):
    stats(f"B 最短持{mh}日", run(close_df, open_df, min_hold=mh))

# C: regime gate
stats("C 大盘闸门", run(close_df, open_df, regime=regime))

# D: 闸门+冷却60
stats("D 闸门+冷却60", run(close_df, open_df, regime=regime, cooldown=60))

# E: 连续站上 MA 确认 (entry confirmation)
for cf in (10, 20, 30):
    stats(f"E 站上确认{cf}日", run(close_df, open_df, conf=cf))

# G: 趋势破位后暂停开新仓
for ep in (5, 10, 20):
    stats(f"G 破位停{ep}日", run(close_df, open_df, entry_pause=ep))

# E+G 组合
stats("E30+G10", run(close_df, open_df, conf=30, entry_pause=10))
