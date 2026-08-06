#!/usr/bin/env python3
"""诚实动态池回测: 上市满3年 / AUM≥10亿 / 相关性去孪生(0.8) / 只进不出, 逐时点自动入池。

回答: "持续更新ETF池" 作为规则化策略, 2015-2026 真实可外推收益是多少。
候选 = 当前23只行业池 + explore 40只探索候选 (2026-08 时点可获得的所有候选)。
局限: AUM 用当前快照 (akshare fund_etf_spot_em), 历史 AUM 无数据源, 属轻微近似。
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
                      BASE_ETF, BT_START, rotation_sim)
from etf_rot_signal import load_benches, plot_results

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
    except Exception as e:
        print(f"      [warn] AUM 拉取失败: {e}, 跳过 AUM 过滤")
        return None


def build_cluster_pool(cands, aum, corr=CORR, factor="aum"):
    """图论版池构建: 相关>=corr 连边 -> 连通分量=行业风格簇 -> 每簇按 factor 选代表。

    factor: "aum" 组内规模最大者 (流动性=实盘可买性最优)
            "oldest" 组内上市最早者 (历史最长, 数据最稳)
    代表入池日仍=上市满3年 (只进不出)。返回 (close_df, open_df, log, groups)。
    """
    codes = sorted(cands)
    n = len(codes)
    ret = pd.DataFrame({code: c["close"].pct_change() for code, c in cands.items()})
    corr_m = ret.corr().loc[codes, codes].fillna(0.0).to_numpy()
    parent = list(range(n))
    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    for i in range(n):
        for j in range(i + 1, n):
            if corr_m[i, j] >= corr:
                parent[find(i)] = find(j)
    for i in range(n):
        find(i)
    groups = {}
    for i, c in enumerate(codes):
        groups.setdefault(parent[i], []).append(c)

    log, admitted = [], []
    for members in groups.values():
        picks = [(cands[m]["close"].index[0], m) for m in members]
        if factor == "aum" and aum is not None:
            picks.sort(key=lambda x: aum.get(x[1], 0.0), reverse=True)
        else:
            picks.sort(key=lambda x: x[0])          # oldest 最早上市
        rep = picks[0][1]
        listing = cands[rep]["close"].index[0]
        admit_day = listing + pd.DateOffset(years=MIN_YEARS)
        admitted.append(rep)
        names = ", ".join(f"{cands[m]['name']}({aum.get(m,0)/1e8:.0f}亿)" if aum else cands[m]["name"]
                          for m in members)
        log.append((rep, cands[rep]["name"], listing, admit_day, "admit",
                    f"簇[{len(members)}]: {names}", members))
    cdf, odf = _to_frames(cands, admitted)
    return cdf, odf, log, groups


def _to_frames(cands, admitted):
    """仅保留 admitted 集合, 入池日(上市满3年)前 NaN。"""
    dates = sorted(set().union(*[set(c["close"].index) for c in cands.values()]))
    close, opn = {}, {}
    for code in admitted:
        c = cands[code]
        day = c["close"].index[0] + pd.DateOffset(years=MIN_YEARS)
        close[code] = c["close"].where(c["close"].index >= day)
        opn[code] = c["open"].where(c["open"].index >= day)
    return (pd.DataFrame(close, index=dates).sort_index().ffill(),
            pd.DataFrame(opn, index=dates).sort_index().ffill())


def build_pool(cands, aum, min_years=MIN_YEARS, order="listing"):
    """按入池日/因子排序, 规则化入池。返回 (close_df, open_df, log)。

    order: "listing" 先到先得 (谁先满3年谁占簇)
           "aum"     组内规模最大者优先 (同簇后入池者被相关剔除)
           "oldest"  组内上市最早者优先
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


def run_cluster(cands, aum, ts):
    """图论版: 连通分量=行业风格簇, 每簇按因子选组内最优, 各因子对比回测。"""
    names = {code: c["name"] for code, c in cands.items()}
    for factor in ("aum", "oldest"):
        close_df, open_df, log, groups = build_cluster_pool(cands, aum, factor=factor)
        reps = [l for l in log if l[4] == "admit"]
        print(f"\n===== 聚类池 factor={factor} (相关>={CORR}连边, 入池{len(reps)}只) =====")
        for rep, name, listing, admit_day, _, ginfo, _ in sorted(reps, key=lambda x: x[3]):
            print(f"  {rep} {name}: 入池 {admit_day.date()}  <- {ginfo}")
        res = rotation_sim(close_df, open_df, ma_n=MA_N, lookback=LOOKBACK, mom_gap=MOM_GAP,
                           min_mom=MIN_MOM, trail=TRAIL, cooldown=COOLDOWN, base_w=BASE_W,
                           base_etf=BASE_ETF, commission=COMMISSION, gate=True, start=BT_START)
        sv = pd.Series(res["navs"], index=res["dates"])
        sm = calc_metrics(sv)
        print(f"  年化 {sm['annual_return']:.1f}% 夏普 {sm['sharpe']:.2f} 回撤 {sm['max_drawdown']:.1f}% "
              f"总收益 {sm['total_return']:.0f}% | 买入 {sum(1 for t in res['trades'] if t['action']=='buy')} "
              f"切换 {sum(1 for t in res['trades'] if t['action']=='switch')}")
        benches = {}
        for bname, s in load_benches().items():
            benches[bname] = s.dropna().reindex(sv.index, method="ffill").dropna()
        title = (f"聚类池 factor={factor} ({ts})<br><sup>相关&gt;={CORR}连通分量=簇, 簇内按{factor}选代表 | "
                 f"策略同v7: MA{MA_N}+动量{LOOKBACK} | 底仓{BASE_ETF} {int(BASE_W*100)}% | {BT_START} ~ 2026-08</sup>")
        with open(f"etf_rot_cluster_{factor}_result_{ts}.html", "w", encoding="utf-8") as f:
            f.write(plot_results(sv, benches, res["trades"], names, ts, title_text=title))


def main():
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    print("[1/3] 读取候选缓存...")
    cands = load_candidates()
    print(f"      候选 {len(cands)} 只: {', '.join(c['name'] for c in cands.values())[:200]}...")

    print("[2/3] 拉取 AUM 快照...")
    aum = fetch_aum()
    if aum:
        print(f"      已拉取 {len(aum)} 只规模")

    print(f"[3/3] 规则化入池 (上市满{MIN_YEARS}年 / AUM≥10亿 / 相关<{CORR} / 只进不出) + 回测...")
    mode = sys.argv[1] if len(sys.argv) > 1 else "dynamic"
    if mode == "cluster":
        run_cluster(cands, aum, ts)
        return
    close_df, open_df, log = build_pool(cands, aum)
    admitted = [l for l in log if l[4] == "admit"]
    print(f"      入池 {len(admitted)}/{len(cands)} 只:")
    for code, name, listing, admit_day, _, _, _ in sorted(admitted, key=lambda x: x[3]):
        print(f"        {code} {name}: 上市 {listing.date()} -> 入池 {admit_day.date()}")
    rej = [l for l in log if l[4] == "reject"]
    print(f"      剔除 {len(rej)} 只:")
    for code, name, _, _, _, why, _ in sorted(rej, key=lambda x: x[2]):
        print(f"        {code} {name}: {why}")

    names = {code: c["name"] for code, c in cands.items()}
    sv, trades = pd.Series(rotation_sim(close_df, open_df, ma_n=MA_N, lookback=LOOKBACK,
                                        mom_gap=MOM_GAP, min_mom=MIN_MOM, trail=TRAIL,
                                        cooldown=COOLDOWN, base_w=BASE_W, base_etf=BASE_ETF,
                                        commission=COMMISSION, gate=True,
                                        start=BT_START)["navs"],
                           index=rotation_sim(close_df, open_df, ma_n=MA_N, lookback=LOOKBACK,
                                              mom_gap=MOM_GAP, min_mom=MIN_MOM, trail=TRAIL,
                                              cooldown=COOLDOWN, base_w=BASE_W, base_etf=BASE_ETF,
                                              commission=COMMISSION, gate=True,
                                              start=BT_START)["dates"]), rotation_sim(
        close_df, open_df, ma_n=MA_N, lookback=LOOKBACK, mom_gap=MOM_GAP, min_mom=MIN_MOM,
        trail=TRAIL, cooldown=COOLDOWN, base_w=BASE_W, base_etf=BASE_ETF,
        commission=COMMISSION, gate=True, start=BT_START)["trades"]

    benches = {}
    for name, s in load_benches().items():
        s = s.dropna().reindex(sv.index, method="ffill").dropna()
        benches[name] = s
    sm = calc_metrics(sv)
    print("\n--- 动态池回测结果 ---")
    print(f"      策略年化收益: {sm['annual_return']:.1f}%")
    print(f"      策略夏普: {sm['sharpe']:.2f}")
    print(f"      策略最大回撤: {sm['max_drawdown']:.1f}%")
    print(f"      总收益: {sm['total_return']:.1f}%")
    print(f"      买入 {sum(1 for t in trades if t['action']=='buy')} 次, 切换 {sum(1 for t in trades if t['action']=='switch')} 次")
    for name, bv in benches.items():
        bm = calc_metrics(bv)
        print(f"      {name}: 年化 {bm['annual_return']:.1f}% 夏普 {bm['sharpe']:.2f} 回撤 {bm['max_drawdown']:.1f}%")

    html_file = f"etf_rot_dynpool_result_{ts}.html"
    title = (f"诚实动态池回测 ({ts})<br><sup>规则: 上市满{MIN_YEARS}年/AUM≥10亿/相关&lt;{CORR}去孪生/只进不出 | "
             f"策略同v7: MA{MA_N}+动量{LOOKBACK} 差&gt;{MOM_GAP:.0%} | 底仓{BASE_ETF} {int(BASE_W*100)}% | "
             f"{BT_START} ~ 2026-08 | 入池{len(admitted)}/候选{len(cands)}</sup>")
    with open(html_file, "w", encoding="utf-8") as f:
        f.write(plot_results(sv, benches, trades, names, ts, title_text=title))
    print(f"      HTML -> {html_file}")


if __name__ == "__main__":
    main()
