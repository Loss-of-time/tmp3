#!/usr/bin/env python3
"""动态池构建 (从 backup/etf_rot_dynpool.py 抽出, 回测与模拟盘共用默认池)。

规则: 候选 = 23只行业池 + explore 候选; 上市满3年 / AUM≥10亿(当前快照近似) /
相关性去孪生(0.8) / 只进不出, 逐时点自动入池 (入池日前 NaN)。
AUM 拉取失败时返回 None 并跳过 AUM 过滤 (离线可跑, 口径降级)。
"""
import glob
import json
import os

import pandas as pd

DIRS = ["cache_bt/etf_industry", "cache_bt/etf_explore"]
MIN_AUM = 10e8
CORR = 0.8
MIN_YEARS = 3
CORR_DAYS = 250


def load_candidates():
    seen = {}
    for d in DIRS:
        for f in sorted(glob.glob(os.path.join(d, "*.json"))):
            j = json.load(open(f))
            if j["code"] in seen:
                continue
            dates = pd.to_datetime(j["dates"])
            seen[j["code"]] = {"name": j["name"],
                               "close": pd.Series(j["close"], index=dates).sort_index(),
                               "open": pd.Series(j["open"], index=dates).sort_index()}
    return seen


def fetch_aum():
    """当前规模快照 {code: 流通市值}。失败返回 None (跳过 AUM 过滤)。"""
    try:
        import akshare as ak
        df = ak.fund_etf_spot_em()
        return dict(zip(df["代码"].astype(str), df["流通市值"].astype(float)))
    except Exception:
        return None


def build_pool(cands, aum, min_years=MIN_YEARS, order="listing"):
    """按入池日/因子排序, 规则化入池。返回 (close_df, open_df, log)。

    order: "listing" 先到先得 (谁先满3年谁占簇)
           "aum"     组内规模最大者优先 (同簇后入池者被相关剔除)
    """
    log = []
    admitted = []          # 已入池 (code, 入池日)
    dates = sorted(set().union(*[set(c["close"].index) for c in cands.values()]))
    def key(item):
        code, c = item
        if order == "aum" and aum is not None:
            return -aum.get(code, 0.0)
        if order == "oldest":
            return c["close"].index[0]
        return c["close"].index[0] + pd.DateOffset(years=min_years)
    for code, c in sorted(cands.items(), key=key):
        s = c["close"]
        listing = s.index[0]
        admit_day = listing + pd.DateOffset(years=min_years)
        # 1) AUM 过滤 (当前快照近似)
        if aum is not None and aum.get(code, 0) < MIN_AUM:
            log.append((code, c["name"], listing, admit_day, "reject",
                        f"AUM {aum.get(code,0)/1e8:.1f}亿 < 10亿", None))
            continue
        # 2) 相关性去孪生: 入池日前 CORR_DAYS 窗口 vs 池内成员
        drop_by, corr = None, None
        if admitted:
            win = s[(s.index >= admit_day - pd.Timedelta(days=400)) & (s.index <= admit_day)]
            ret = win.pct_change().dropna()
            for mcode, _ in admitted:
                ms = cands[mcode]["close"].reindex(ret.index)
                mret = ms.pct_change().dropna()
                both = pd.concat([ret, mret], axis=1).dropna()
                if len(both) < 60:
                    continue
                r = both.iloc[:, 0].corr(both.iloc[:, 1])
                if r >= CORR:
                    drop_by, corr = mcode, r
                    break
        if drop_by:
            log.append((code, c["name"], listing, admit_day, "reject",
                        f"与 {cands[drop_by]['name']} 相关 {corr:.2f} >= 0.8", drop_by))
            continue
        admitted.append((code, admit_day))
        log.append((code, c["name"], listing, admit_day, "admit", "", None))
        # 入池前 NaN (上市满 min_years 才可用)
        c["close"] = s.where(s.index >= admit_day)
        c["open"] = c["open"].where(c["open"].index >= admit_day)
    close_df = pd.DataFrame({code: c["close"] for code, c in cands.items()}, index=dates).sort_index().ffill()
    open_df = pd.DataFrame({code: c["open"] for code, c in cands.items()}, index=dates).sort_index().ffill()
    return close_df, open_df, log
