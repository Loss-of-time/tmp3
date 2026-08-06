#!/usr/bin/env python3
"""行业 PE 分位估值因子实验: 707只股票按证监会行业聚合 PE 中位数 -> 10年滚动分位
-> 对 v7 轮动做估值过滤/打分, 回测对比基线 mom180 23.2%。

无 PE 的 ETF(黄金/纳指/恒生科技/旅游)视为中性(不过滤)。
"""

import glob
import json
import os
import sys
from datetime import datetime

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from backtest import COMMISSION, calc_metrics
from rot_core import (MA_N, LOOKBACK, MOM_GAP, MIN_MOM, TRAIL, COOLDOWN, BASE_W,
                      BASE_ETF, BT_START)
from etf_rot_signal import load_data, load_benches

# 23只 ETF -> baostock 行业代码(证监会2012); None = 无 PE 中性
IND_MAP = {
    "512480": "C39", "515050": "C39",           # 半导体/5G -> 计算机通信电子
    "512000": "J67",                             # 券商 -> 资本市场服务
    "512800": "J66",                             # 银行 -> 货币金融
    "512660": "C37",                             # 军工 -> 铁路船舶航空航天
    "512690": "C15",                             # 白酒
    "512010": "C27",                             # 医药
    "512400": "C32",                             # 有色
    "515220": "B06",                             # 煤炭
    "512980": ["I64", "R86", "R87"],             # 传媒: 互联网+新闻出版+影视
    "516020": "C26",                             # 化工
    "159928": ["C13", "C14", "C15"],             # 消费: 农副+食品+酒
    "515030": ["C36", "C38"],                    # 新能源车: 汽车+电气机械
    "515790": "C38",                             # 光伏
    "512890": "__ALL__", "510880": "__ALL__",    # 红利: 全市场
    "159611": "D44",                             # 电力
    "159825": ["A01", "A02", "A03", "A04", "A05"],  # 农业
    "562500": ["C34", "C35"],                    # 机器人: 通用+专用设备
    "518880": None, "513100": None, "513180": None, "159766": None,
}
QUANTILE_YEARS = 10


def load_stocks():
    """707 只股票 {code: {dates: idx, pe: Series}}"""
    stocks = {}
    for f in glob.glob("cache_bt/stocks/*.json"):
        d = json.load(open(f))
        dates = pd.to_datetime(d["dates"])
        stocks[d["code"]] = pd.Series(d["pe"], index=dates)
    return stocks


def industry_pe(stocks, ind_map):
    """{行业代码: 日频 PE 中位数 Series}。__ALL__ = 全市场。"""
    pe = pd.DataFrame({code: s for code, s in stocks.items()})
    pe = pe.replace(0.0, np.nan)
    out = {}
    for ind in set(x for v in ind_map.values() if v for x in (v if isinstance(v, list) else [v])):
        if ind == "__ALL__":
            out[ind] = pe.median(axis=1, skipna=True)
        else:
            cols = [c for c, s in stocks.items() if _ind_of(c, ind)]
            out[ind] = pe[cols].median(axis=1, skipna=True)
    return out


_IND_MAP = None
def _ind_of(code, ind):
    global _IND_MAP
    if _IND_MAP is None:
        _IND_MAP = json.load(open("/tmp/opencode/ind_map.json"))
    return _IND_MAP.get(code, "").startswith(ind)


def pe_quantile_df():
    """日频 × 23 ETF: 行业 PE 10年滚动分位 (无PE=NaN)。"""
    stocks = load_stocks()
    inds = industry_pe(stocks, IND_MAP)
    q = {}
    for code, ind in IND_MAP.items():
        if ind is None:
            continue
        keys = ["__ALL__"] if ind == "__ALL__" else (ind if isinstance(ind, list) else [ind])
        if ind == "__ALL__":
            s = inds["__ALL__"]
        else:
            cols = [c for c in inds if c in set(keys)]
            s = pd.concat([inds[c] for c in cols], axis=1).median(axis=1, skipna=True)
        q[code] = s.rolling(int(QUANTILE_YEARS * 250), min_periods=250).rank(pct=True)
    q = pd.DataFrame(q).sort_index()
    for code, ind in IND_MAP.items():      # 无 PE 的 ETF 全 NaN 列
        if ind is None:
            q[code] = np.nan
    return q


def score_sim(close_df, open_df, peq, mode, thr=0.90, w=0.5, thr_pos="high", start=BT_START):
    """rot_core.rotation_sim 复制版: 动量选股 + PE 分位过滤/打分。

    mode: "none" 基线 / "filter" 剔除 PE 分位>thr 候选 / "combo" score=w*rank(mom)+(1-w)*rank(1-peq)
    动量信号与 MA200 门控/止损/冷却/底仓 全同 v7。
    """
    if start is not None:
        close_df = close_df.loc[close_df.index >= pd.Timestamp(start)]
        open_df = open_df.loc[open_df.index >= pd.Timestamp(start)]
        peq = peq.reindex(close_df.index)
    sig_close = close_df.shift(1)
    ma = sig_close.rolling(MA_N).mean()
    above = sig_close > ma
    mom = sig_close.pct_change(LOOKBACK)
    peak60 = sig_close.rolling(61, min_periods=1).max()
    lowvol = close_df[BASE_ETF].fillna(0.0)
    first_px = lowvol[lowvol > 0].iloc[0]
    lowvol = lowvol.replace(0.0, first_px)
    fill = open_df if open_df is not None else close_df
    base_shares = BASE_W / lowvol.iloc[0]

    rank_mom = mom.rank(axis=1, pct=True)
    rank_pe = peq.rank(axis=1, pct=True) if peq is not None else None

    rot_cash = 1.0 - BASE_W
    shares = 0.0
    code = None
    peak = 0.0
    last_sell = {}
    trades = []
    dates, navs = [], []

    for i, date in enumerate(close_df.index):
        px = sig_close.loc[date, code] if code else None
        signal = above.loc[date]
        day_trades = []
        if code is not None:
            peak = max(peak, px)
            reason = None
            if not bool(signal[code]):
                reason = "trend"
            elif px <= peak * (1 - TRAIL):
                reason = "trail"
            if reason:
                exec_px = fill.loc[date, code]
                rot_cash += shares * exec_px * (1 - COMMISSION)
                last_sell[code] = i
                shares = 0.0
                code = None
                peak = 0.0
                day_trades.append({"date": date, "action": "sell", "code": None,
                                   "reason": reason})
        elig = [c for c in signal[signal].index
                if not np.isnan(mom.loc[date, c]) and mom.loc[date, c] > MIN_MOM
                and (COOLDOWN == 0 or i - last_sell.get(c, -10**9) > COOLDOWN)]
        if mode == "filter" and thr_pos == "high":
            elig = [c for c in elig if np.isnan(peq.loc[date, c]) or peq.loc[date, c] <= thr]
        if elig:
            elig = [c for c in elig if sig_close.loc[date, c] >= peak60.loc[date, c] * (1 - TRAIL)]
        if len(elig) > 0:
            if mode == "combo":
                sc = w * rank_mom.loc[date, elig] + (1 - w) * (1 - rank_pe.loc[date, elig].fillna(0.5))
                best = sc.idxmax()
            else:
                best = mom.loc[date, elig].idxmax()
            best_px = fill.loc[date, best]
            if code is None:
                target = rot_cash
                shares = target / best_px
                rot_cash -= target + target * COMMISSION
                code = best
                peak = best_px
                day_trades.append({"date": date, "action": "buy", "code": best,
                                   "reason": "new"})
            elif best != code:
                cur_mom = mom.loc[date, code]
                best_mom = mom.loc[date, best]
                if best_mom - cur_mom > MOM_GAP:
                    old = code
                    exec_px = fill.loc[date, old]
                    rot_cash += shares * exec_px * (1 - COMMISSION)
                    shares = 0.0
                    target = rot_cash
                    shares = target / best_px
                    rot_cash -= target + target * COMMISSION
                    code = best
                    peak = best_px
                    day_trades.append({"date": date, "action": "switch", "code": best,
                                       "reason": f"换 {old}"})
        px = close_df.loc[date, code] if code else None
        mv = base_shares * lowvol.iloc[i] + rot_cash + (shares * px if code else 0)
        dates.append(date)
        navs.append(mv)
        trades.extend(day_trades)
    return pd.Series(navs, index=dates), trades


def main():
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    print("[1/3] 构建行业 PE 分位...")
    peq = pe_quantile_df()
    etfs, opens, _ = load_data()
    close_df = pd.DataFrame(etfs).sort_index().ffill()
    open_df = pd.DataFrame(opens).sort_index().reindex(close_df.index).ffill()
    common = close_df.index.intersection(peq.index)
    peq = peq.reindex(common)
    cov = peq.notna().mean()
    print(f"      行业PE分位覆盖: {dict(cov.round(2))}")

    print("[2/3] 回测对比...")
    benches = load_benches()
    for mode, kw in [("none", {}), ("filter", {"thr": 0.90}), ("filter", {"thr": 0.80}),
                     ("combo", {"w": 0.5}), ("combo", {"w": 0.7})]:
        sv, trades = score_sim(close_df, open_df, peq, mode, **kw)
        sm = calc_metrics(sv)
        tag = mode + (" thr=%.2f" % kw["thr"] if "thr" in kw else (" w=%.1f" % kw["w"] if "w" in kw else ""))
        print(f"      {tag:22s}: 年化 {sm['annual_return']:5.1f}% 夏普 {sm['sharpe']:.2f} "
              f"回撤 {sm['max_drawdown']:5.1f}% 总收益 {sm['total_return']:5.0f}% | "
              f"买 {sum(1 for t in trades if t['action']=='buy')} 切 {sum(1 for t in trades if t['action']=='switch')}")

    print("[3/3] PE 分位 vs 未来收益 方向验证 (IC) ...")
    ret = close_df.pct_change()
    for n in (20, 60, 120):
        fwd = close_df.shift(-n) / close_df - 1
        ic = []
        for d in peq.index:
            a = peq.loc[d].dropna()
            b = fwd.loc[d].reindex(a.index)
            both = pd.concat([a, b], axis=1).dropna()
            if len(both) >= 5:
                ic.append(both.iloc[:, 0].corr(both.iloc[:, 1], method="pearson"))
        ic = pd.Series(ic).dropna()
        print(f"      IC(PE分位, 未来{n}日收益): 均值 {ic.mean():.4f} t {ic.mean()/ic.std()*np.sqrt(len(ic)):.2f} 胜率 {(ic>0).mean():.2f} 样本 {len(ic)}")


if __name__ == "__main__":
    main()
