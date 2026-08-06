"""实验: 给 v7 信号轮动加短期趋势过滤/合并评分, 观察是否避免"刚崩就买"。

过滤模式(在 elig 上叠加):
  none: 无过滤 (现状, 只看180日动量>0)
  B: mom20 > 0   (20日动量必须为正)
  C: close > ma20 (收盘站上20日均线)
  D: mom60 > 0   (60日动量必须为正)
  E: 距自身60日峰值回撤 < trail (不接飞刀)
  F: E + mom20 > -10%
  G: 双动量合并评分 score = mom + w_short*mom20, 用 score 选标/切换 (非硬门槛)
  H: G + E(不接飞刀) 评分过滤
  I: 汉斯复合动量 score = 25日回归斜率年化 * R², score>0 才可入, 用 score 选标
"""
import sys
import numpy as np
import pandas as pd

sys.path.insert(0, ".")
from etf_rot_signal import ETFS_DIR, BASE_ETF, BT_START, load_data
from etf_rot_signal import rotation_backtest as _orig
import etf_rot_signal as S
from backtest import calc_metrics


def rotation_backtest(close_df, open_df=None, bt_start=S.BT_START, ma_n=S.MA_N, lookback=S.LOOKBACK,
                      mom_gap=S.MOM_GAP, min_mom=S.MIN_MOM, trail=S.TRAIL,
                      base_w=S.BASE_W, base_etf=S.BASE_ETF, cooldown=S.COOLDOWN,
                      filter_mode="none", w_short=0.5):
    """同 etf_rot_signal.rotation_backtest (昨日信号+今日开盘成交), 但加短期过滤/合并评分。"""
    bt = close_df[bt_start:]
    sig_close = bt.shift(1)
    ma = sig_close.rolling(ma_n).mean()
    above = sig_close > ma
    mom = sig_close.pct_change(lookback)
    fill = open_df[bt_start:] if open_df is not None else bt
    mom20 = sig_close.pct_change(20)
    mom60 = sig_close.pct_change(60)
    ma20 = sig_close.rolling(20).mean()
    lowvol = bt[base_etf]

    # 汉斯复合动量: 25日回归斜率年化 * R²
    if filter_mode in ("G", "H", "I"):
        if filter_mode == "I":
            n = 25
            x = np.arange(1, n + 1)
            logc = np.log(bt)
            slope = logc.rolling(n).apply(
                lambda w: np.polyfit(x, w, 1)[0], raw=True)
            r2 = logc.rolling(n).apply(
                lambda w: np.corrcoef(x, w)[0, 1] ** 2, raw=True)
            score = slope * 252 * r2
        else:
            score = mom + w_short * mom20

    values = pd.Series(index=bt.index, dtype=float)
    base_shares = base_w / lowvol.iloc[0]
    rot_cash = 1.0 - base_w
    shares = 0.0
    code = None
    peak = 0.0
    last_sell = {}
    trades = []

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
                proceeds = shares * exec_px * (1 - S.COMMISSION)
                rot_cash += proceeds
                last_sell[code] = i
                shares = 0.0
                code = None
                peak = 0.0
                trades.append({"date": date, "action": "sell", "code": None, "reason": reason})

        elig = [c for c in signal[signal].index
                if not np.isnan(mom.loc[date, c]) and mom.loc[date, c] > min_mom
                and (cooldown == 0 or i - last_sell.get(c, -10**9) > cooldown)]
        if filter_mode == "B" and elig:
            elig = [c for c in elig if mom20.loc[date, c] > 0]
        elif filter_mode == "C" and elig:
            elig = [c for c in elig if sig_close.loc[date, c] > ma20.loc[date, c]]
        elif filter_mode == "D" and elig:
            elig = [c for c in elig if mom60.loc[date, c] > 0]
        elif filter_mode in ("E", "F", "H") and elig:
            # 不接飞刀: 距自身近期峰值(60日)回撤必须 < trail (基于昨日收盘)
            peak60 = sig_close.iloc[max(0, i-60):i+1].max(axis=0)
            elig = [c for c in elig if sig_close.loc[date, c] >= peak60[c] * (1 - trail)]
            if filter_mode == "F":
                elig = [c for c in elig if mom20.loc[date, c] > -0.10]
        elif filter_mode == "I" and elig:
            elig = [c for c in elig if score.loc[date, c] > 0]

        if len(elig) > 0:
            ref = score if filter_mode in ("G", "H", "I") else mom
            best = ref.loc[date, elig].idxmax()
            best_px = fill.loc[date, best]
            if code is None:
                target = rot_cash
                shares = target / best_px
                cost = target * S.COMMISSION
                rot_cash -= target + cost
                code = best
                peak = best_px
                trades.append({"date": date, "action": "buy", "code": best,
                               "reason": "new", "mom": round(float(ref.loc[date, best]) * 100, 1)})
            elif best != code:
                cur_mom = ref.loc[date, code]
                best_mom = ref.loc[date, best]
                if best_mom - cur_mom > mom_gap:
                    old_code = code
                    exec_px = fill.loc[date, old_code]
                    proceeds = shares * exec_px * (1 - S.COMMISSION)
                    rot_cash += proceeds
                    target = rot_cash
                    shares = target / best_px
                    cost = target * S.COMMISSION
                    rot_cash -= target + cost
                    code = best
                    peak = best_px
                    trades.append({"date": date, "action": "switch", "code": best,
                                   "reason": f"换 {old_code}",
                                   "mom": round(float(best_mom) * 100, 1)})

        px = close_df.loc[date, code] if code else None
        mv = base_shares * lowvol.iloc[i] + rot_cash + (shares * px if code else 0)
        values.iloc[i] = mv

    return values, trades


def main():
    etfs, _, hs300 = load_data()
    close_df = pd.DataFrame(etfs)
    bench = hs300

    # 缩到与回测一致的时段
    bt = close_df[BT_START:]
    bench_bt = bench.reindex(bt.index).ffill()

    for mode, kw in [
        ("none", {}),
        ("B", {}), ("C", {}), ("D", {}),
        ("E", {}), ("F", {}),
        ("G", {}), ("H", {}),
        ("I", {}),
    ]:
        vals, trades = rotation_backtest(close_df, filter_mode=mode, **kw)
        m = calc_metrics(vals)
        bm = calc_metrics(bench_bt)
        buys = [t for t in trades if t["action"] in ("buy", "switch")]
        print(f"[{mode:4s}] 年化{m['annual_return']:.1f}% 夏普{m['sharpe']:.2f} 回撤{m['max_drawdown']:.1f}% | "
              f"基准年化{bm['annual_return']:.1f}% | 买入{len(buys)}")
        # 每年收益
        sy = vals.resample("YE").last().pct_change().dropna() * 100
        print("   年度:", {str(d.year): f"{v:.1f}" for d, v in sy.items()})

    # G/H 对 w_short 敏感性
    for w in [0.2, 0.5, 1.0, 2.0]:
        for mode in ["G", "H"]:
            vals, trades = rotation_backtest(close_df, filter_mode=mode, w_short=w)
            m = calc_metrics(vals)
            print(f"[{mode} w={w:.1f}] 年化{m['annual_return']:.1f}% 夏普{m['sharpe']:.2f} 回撤{m['max_drawdown']:.1f}%")


if __name__ == "__main__":
    main()
