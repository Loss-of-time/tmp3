#!/usr/bin/env python3
"""v7 信号调仓共享核心: etf_rot_signal.py 回测 与 paper_trade_signal.py 模拟盘共用。

规则 (单一事实来源, 两边漂移会直接破坏回测-实盘一致性):
- 每日用昨日收盘信号决策, 今日开盘价成交 (避免前视偏差)
- 离场: 跌破 MA_N (trend) 或峰值回撤 TRAIL (trail)
- 入场: 站上 MA_N 且动量 > MIN_MOM, 空仓买动量最强
- 切换: 候选动量超过持仓 MOM_GAP 绝对差才切
- 冷却: 卖出后 COOLDOWN 天内不买回同标的
- 不接飞刀: 距自身 60 日峰值回撤 >= TRAIL 的候选拦停
- 部分止盈: 轮动仓浮盈达 TP_HALF 卖 TP_FRAC 落袋, 剩余仍跟 trail/趋势 (落袋现金参与下次买入)
- 下跌速度止损: 收盘较 DROP_N 日前跌 >= DROP_X 即离场, 与 trail 叠加 (20d8% 定稿)
"""

import numpy as np
import pandas as pd

# --- 策略参数 ---
MA_N = 200          # 牛熊线: 只买收盘 > MA_N 的 ETF, 跌破则趋势破坏离场
LOOKBACK = 180      # 动量窗口(交易日)
MOM_GAP = 1.0       # 切换阈值: 候选动量超过持仓动量此比例(绝对差)才切换
MIN_MOM = 0.0       # 入场门槛: 动量必须 > 此值才可买入
MOM_CAP = None      # 动量上限: 180日动量 >= 此比例不买入 (None=关闭, 过热过滤实验)
TRAIL = 0.20        # 移动止损: 从持仓峰值回撤此比例离场
TP_HALF = 0.8       # 部分止盈: 轮动仓浮盈达此比例卖半落袋 (None=关闭, 剩余仍跟 trail)
TP_FRAC = 0.5       # 部分止盈卖出占比: 触发 TP_HALF 时卖出仓位的此比例 (0.5=卖半, 联合调优定稿)
DROP_N = 20         # 下跌速度止损窗口(交易日): 收盘较 DROP_N 日前跌 >= DROP_X 即离场 (0=关闭)
DROP_X = 0.08       # 下跌速度止损阈值: 与 trail 叠加, 任一触发即离场
COOLDOWN = 20       # 冷却期: 卖出后此天数内不买回同标的
BASE_W = 0.45       # 底仓权重
BASE_ETF = "518880" # 底仓标的: "512890" 红利低波 / "513100" 纳指ETF(溢价可接受) / "518880" 黄金(长周期唯一单调稳健防御)
BT_START = "2016-08-11"
CASH_APR = 0.02
ETFS_DIR = "cache_bt/etf_industry"


def rotation_sim(close_df, open_df=None, *, ma_n=MA_N, lookback=LOOKBACK,
                 mom_gap=MOM_GAP, min_mom=MIN_MOM, mom_cap=MOM_CAP,
                 trail=TRAIL, tp_half=TP_HALF, tp_frac=TP_FRAC, tp_reset=False,
                 cooldown=COOLDOWN,
                  base_w=BASE_W, base_etf=BASE_ETF, commission=0.001, gate=True,
                  start=None, shift_full=False, tradable=None, drop_n=DROP_N, drop_x=DROP_X,
                  pick_rank=0):
    """逐日重放 v7 轮动策略。返回 {"dates", "navs", "trades", "last"}。

    start: 起始日期(含)。shift_full=False (回测): 信号在窗口内 shift,
    起跑首日信号为 NaN; shift_full=True (模拟盘): 信号基于全量历史,
    起跑日可用前一交易日信号 (模拟盘从中途开户, 首日即可按信号入场)。
    gate=False 关闭 MA_N 牛熊线(入池门槛+趋势离场), 纯动量+移动止损。
    底仓上市晚于起跑日时, 上市前按现金等值持有(不产生收益)。
    tradable: bool DataFrame 同 index/columns (dynpool.build_pool 产出),
    False=无真实行情(入池前/停牌) -> 该标的当日不可买不可卖, 持仓价格冻结。
    drop_n/drop_x: 下跌速度止损 (定稿): 收盘较 drop_n 日前跌 >= drop_x 即离场
    (0=关闭; 与 trail 叠加, 任一触发即离场)。trail=None 关闭峰值回撤止损。
    pick_rank: 0=买动量第一 (默认), 1=买动量第二 (实验), 依此类推。
    """
    if shift_full:
        sig_close = close_df.shift(1)          # 模拟盘: 全量信号, 起跑日可用前一交易日
    else:
        sig_close = close_df.shift(1)          # 回测: 同上 (指标用起跑前历史预热, 非未来函数)
    ma = sig_close.rolling(ma_n).mean()
    above = (pd.DataFrame(True, index=sig_close.index, columns=sig_close.columns)
             if not gate else (sig_close > ma))
    mom = sig_close.pct_change(lookback)
    peak60 = sig_close.rolling(61, min_periods=1).max()   # 60日峰值(含当日)
    drop_ref = sig_close.shift(drop_n) if drop_n > 0 else None
    if start is not None:
        win = close_df.index >= pd.Timestamp(start)
        close_df = close_df.loc[win]
        sig_close = sig_close.loc[win]
        ma, above, mom, peak60 = (x.loc[win] for x in (ma, above, mom, peak60))
        if drop_ref is not None:
            drop_ref = drop_ref.loc[win]
        if open_df is not None:
            open_df = open_df.loc[win]
        if tradable is not None:
            tradable = tradable.loc[win]
    lowvol = close_df[base_etf].fillna(0.0)
    first_px = lowvol[lowvol > 0].iloc[0]
    lowvol = lowvol.replace(0.0, first_px)
    fill = open_df if open_df is not None else close_df
    base_shares = base_w / lowvol.iloc[0]

    rot_cash = 1.0 - base_w
    shares = 0.0
    code = None
    peak = 0.0
    cost = 0.0
    half_done = False
    last_sell = {}
    trades = []
    dates, navs = [], []
    last = None

    for i, date in enumerate(close_df.index):
        px = sig_close.loc[date, code] if code else None
        signal = above.loc[date]
        day_trades = []
        can = (lambda c: True) if tradable is None else (
            lambda c: bool(tradable.loc[date, c]))

        # 持仓每日检查 (昨日收盘信号): 趋势破坏 / 峰值回撤 / 下跌速度
        if code is not None:
            peak = max(peak, px)
            reason = None
            if can(code):
                if not bool(signal[code]):
                    reason = "trend"
                elif trail is not None and px <= peak * (1 - trail):
                    reason = "trail"
                elif drop_ref is not None and px <= drop_ref.loc[date, code] * (1 - drop_x):
                    reason = "drop"
                elif tp_half and tp_frac > 0 and not half_done and px >= cost * (1 + tp_half):
                    exec_px = fill.loc[date, code]
                    rot_cash += shares * tp_frac * exec_px * (1 - commission)
                    shares *= (1 - tp_frac)
                    half_done = True
                    day_trades.append({"date": date, "action": "tp_half", "code": code,
                                       "reason": f"浮盈{tp_half:.0%}卖半"})
                    if tp_reset and shares == 0:
                        # 全卖后释放占位: 等同立即离场, 当日信号即可重新入场 (占位=被原标的事先拴住)
                        last_sell[code] = i
                        code = None
                        peak = 0.0
            if reason:
                exec_px = fill.loc[date, code]
                rot_cash += shares * exec_px * (1 - commission)
                last_sell[code] = i
                shares = 0.0
                code = None
                peak = 0.0
                half_done = False
                day_trades.append({"date": date, "action": "sell", "code": None,
                                   "reason": reason})

        # 每日信号检视 (昨日收盘信号)
        elig = [c for c in signal[signal].index
                if can(c)
                and not np.isnan(mom.loc[date, c]) and mom.loc[date, c] > min_mom
                and (mom_cap is None or mom.loc[date, c] < mom_cap)
                and (cooldown == 0 or i - last_sell.get(c, -10**9) > cooldown)]
        # 不接飞刀: 距自身近期峰值(60日)回撤 >= TRAIL 的候选视为刚崩, 拦停
        if elig and trail is not None:
            elig = [c for c in elig if sig_close.loc[date, c] >= peak60.loc[date, c] * (1 - trail)]
        if len(elig) > 0:
            order = mom.loc[date, elig].sort_values(ascending=False)
            best = order.index[min(pick_rank, len(order) - 1)]
            best_px = fill.loc[date, best]
            if code is None:
                target = rot_cash
                shares = target / best_px
                rot_cash -= target + target * commission
                code = best
                peak = best_px
                cost = best_px
                half_done = False
                day_trades.append({"date": date, "action": "buy", "code": best,
                                   "reason": "new",
                                   "mom": round(float(mom.loc[date, best]) * 100, 1)})
            elif best != code:
                cur_mom = mom.loc[date, code]
                best_mom = mom.loc[date, best]
                if best_mom - cur_mom > mom_gap:
                    old = code
                    exec_px = fill.loc[date, old]
                    rot_cash += shares * exec_px * (1 - commission)
                    shares = 0.0
                    target = rot_cash
                    shares = target / best_px
                    rot_cash -= target + target * commission
                    code = best
                    peak = best_px
                    cost = best_px
                    half_done = False
                    day_trades.append({"date": date, "action": "switch", "code": best,
                                       "reason": f"换 {old}",
                                       "mom": round(float(best_mom) * 100, 1)})

        px = close_df.loc[date, code] if code else None
        mv = base_shares * lowvol.iloc[i] + rot_cash + (shares * px if code else 0)
        dates.append(date)
        navs.append(mv)
        trades.extend(day_trades)
        last = {"date": date, "nav": mv, "code": code,
                "mom": None if code is None else float(mom.loc[date, code]),
                "days": i + 1,
                "cash_pct": rot_cash / mv if code is None else None,
                "cooldowns": [c for c, si in last_sell.items() if i - si <= cooldown],
                "candidates": [{"code": c, "name": "", "mom": float(mom.loc[date, c]),
                                "holding": c == code,
                                "cool": c in last_sell and i - last_sell[c] <= cooldown}
                               for c in elig]}

    return {"dates": dates, "navs": navs, "trades": trades, "last": last}
