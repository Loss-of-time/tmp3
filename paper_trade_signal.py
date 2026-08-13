#!/usr/bin/env python3
"""v7 信号调仓策略模拟盘 (paper trading)。

每日增量更新行情缓存 -> 按 v7 信号规则 (etf_rot_signal.py) 从起始日幂等重放
模拟账户 -> 输出报告 (docs/index.html, GitHub Pages 展示)。

v7 规则 (修复 v5 相位锚定伪 alpha):
- 每日检视, 无固定调仓周期
- 离场: 跌破 MA_N (trend) 或峰值回撤 TRAIL (trail)
- 入场: 站上 MA_N 且动量 > MIN_MOM, 空仓买动量最强
- 切换: 候选动量超过持仓 MOM_GAP 绝对差才切
- 冷却期 COOLDOWN: 卖出后 N 天内不买回同标的
"""

import json
import os
from datetime import date

import numpy as np
import pandas as pd

from backtest import COMMISSION, calc_metrics
from rot_core import (MA_N, LOOKBACK, MOM_GAP, MIN_MOM, TRAIL, COOLDOWN, BASE_W,
                      BASE_ETF, rotation_sim, TP_HALF, TP_FRAC, DROP_N, DROP_X)
from paper_trade import incremental_fetch
from etf_rot_signal import plot_results
import dynpool

STATE_FILE = "paper_signal_state.json"
REPORT_DIR = "docs"
NAV_FILE = os.path.join(REPORT_DIR, "data", "nav.json")
HS300_CACHE = "cache_bt/hs300.json"
BENCH_CACHES = {
    "纳指": "cache_bt/ixic.json",
    "上证": "cache_bt/sh000001.json",
}



def _tencent_fetch(code, start, end):
    import urllib.request

    url = (f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
           f"?param={code},day,{start},{end},640,qfq")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    j = json.load(urllib.request.urlopen(req, timeout=15))
    day = j["data"][code].get("day") or j["data"][code].get("qfqday")
    return [(r[0], round(float(r[2]), 4)) for r in day]


def update_bench():
    """增量更新对比基准: 纳指(akshare 全量重拉) + 上证(腾讯增量)。失败用旧缓存。"""
    # 上证: 腾讯增量
    try:
        d = json.load(open(BENCH_CACHES["上证"]))
        last = d["dates"][-1]
        start = (pd.Timestamp(last) - pd.Timedelta(days=10)).strftime("%Y-%m-%d")
        end = date.today().strftime("%Y-%m-%d")
        rows = _tencent_fetch("sh000001", start, end)
        new_dates = [ts for ts, _ in rows]
        new_close = [c for _, c in rows]
        keep_mask = [ts > last for ts in new_dates]
        d["dates"] += [x for x, k in zip(new_dates, keep_mask) if k]
        d["close"] += [x for x, k in zip(new_close, keep_mask) if k]
        with open(BENCH_CACHES["上证"], "w") as fh:
            json.dump(d, fh, ensure_ascii=False)
        print(f"上证: +{sum(keep_mask)} 行 (至 {d['dates'][-1]})")
    except Exception as e:
        print(f"上证 更新失败, 用旧缓存: {str(e)[:80]}")

    # 纳指: akshare 全量重拉覆盖
    try:
        import akshare as ak
        df = ak.index_us_stock_sina(symbol=".IXIC")
        d = {"code": "usIXIC",
             "dates": [str(x) for x in df["date"]],
             "close": [round(float(x), 4) for x in df["close"]]}
        with open(BENCH_CACHES["纳指"], "w") as fh:
            json.dump(d, fh, ensure_ascii=False)
        print(f"纳指: 全量 {len(d['dates'])} 行 (至 {d['dates'][-1]})")
    except Exception as e:
        print(f"纳指 更新失败, 用旧缓存: {str(e)[:80]}")


def update_hs300():
    """增量更新沪深300指数 (sh000300) 到最新交易日。失败用旧缓存。"""
    import urllib.request

    try:
        d = json.load(open(HS300_CACHE))
        last = d["dates"][-1]
        start = (pd.Timestamp(last) - pd.Timedelta(days=10)).strftime("%Y-%m-%d")
        end = date.today().strftime("%Y-%m-%d")
        url = (f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
               f"?param=sh000300,day,{start},{end},640,qfq")
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        j = json.load(urllib.request.urlopen(req, timeout=15))
        day = j["data"]["sh000300"].get("day") or j["data"]["sh000300"].get("qfqday")
        new_dates = [r[0] for r in day]
        new_close = [round(float(r[2]), 4) for r in day]
        keep_mask = [ts > last for ts in new_dates]
        d["dates"] += [x for x, k in zip(new_dates, keep_mask) if k]
        d["close"] += [x for x, k in zip(new_close, keep_mask) if k]
        with open(HS300_CACHE, "w") as fh:
            json.dump(d, fh, ensure_ascii=False)
        added = sum(keep_mask)
        print(f"沪深300: +{added} 行 (至 {d['dates'][-1]})")
    except Exception as e:
        print(f"沪深300 更新失败, 用旧缓存: {str(e)[:80]}")




def _cand_file(code):
    for d in dynpool.DIRS:
        f = os.path.join(d, f"{code}.json")
        if os.path.exists(f):
            return f
    return None


def load_close_df():
    """读候选缓存, 最新日期早于今天则增量更新, 构建动态池。返回 (close_df, open_df, names, tradable)。"""
    cands = dynpool.load_candidates()
    names = {c: d["name"] for c, d in cands.items()}
    need_update = [c for c, d in cands.items()
                   if pd.Timestamp(d["close"].index[-1]) < pd.Timestamp(date.today())]
    if need_update:
        print(f"增量更新 {len(need_update)} 只: {[names[c] for c in need_update]}")
        for code in need_update:
            try:
                incremental_fetch(code, names[code], path=_cand_file(code))
            except Exception as e:
                print(f"  {code} 更新失败, 用缓存: {str(e)[:80]}")
        cands = dynpool.load_candidates()      # 重读含更新
    close_df, open_df, _, tradable = dynpool.build_pool(cands, dynpool.fetch_aum())
    return close_df, open_df, names, tradable


def load_state():
    if os.path.exists(STATE_FILE):
        return json.load(open(STATE_FILE))
    return None


def save_state(state):
    with open(STATE_FILE, "w") as fh:
        json.dump(state, fh, ensure_ascii=False, indent=2)


def simulate(state, close_df, open_df=None, tradable=None, names=None):
    """从模拟账户起始日完整重放至最新交易日。幂等: 每次运行都从 start 重放,
    不依赖数据框行数。返回 (state, today_signal)。

    逐日规则在 rot_core.rotation_sim (与回测共用, 防漂移)。
    成交模型: 今日用昨日收盘信号决策, 今日开盘价成交 (避免前视偏差)。
    """
    if state is None:
        state = {"start": str(close_df.index[-1].date()), "nav_history": [], "trades": []}
    state["nav_history"] = []
    state["trades"] = []

    sim = rotation_sim(close_df, open_df, commission=COMMISSION, start=state["start"],
                       shift_full=True, tradable=tradable)
    prev = close_df.index[close_df.index < pd.Timestamp(state["start"])]
    day0 = str(prev[-1].date()) if len(prev) else state["start"]
    state["nav_history"] = [{"date": day0, "nav": 1.0}] + [
        {"date": str(d.date()), "nav": round(n, 4)}
        for d, n in zip(sim["dates"], sim["navs"])]
    state["trades"] = [dict(t, date=str(t["date"].date())) for t in sim["trades"]]

    last = sim["last"]
    cands = sorted(last["candidates"], key=lambda x: -x["mom"])
    today_signal = {
        "code": last["code"], "name": None if last["code"] is None else "",
        "mom": last["mom"], "days": last["days"], "cash_pct": last["cash_pct"],
        "cooldowns": last["cooldowns"], "candidates": last["candidates"],
        "rank": cands[:10], "next_txt": next_action_txt(sim, last, close_df, names),
        "triggers": trigger_lines(sim, last, close_df, open_df),
    }
    return state, today_signal


def next_action_txt(sim, last, close_df, names):
    """明日操作文案(纯展示, 不复制 rotation_sim 决策): 用 last 状态 + MOM_GAP 门槛推导。"""
    code = last["code"]
    cands = sorted(last["candidates"], key=lambda x: -x["mom"])
    best = cands[0] if cands else None
    disp = lambda c: f"{names.get(c, c)} ({c})" if c else "-"
    if code is None:
        if best is None:
            return "明日: 无合格候选, 轮动仓维持空仓"
        return (f"明日: 开盘买入 {disp(best['code'])} (动量{best['mom']*100:+.1f}%)"
                f" - 若明日收盘触发止跌/止损则放弃")
    txt = f"明日: 持有 {disp(code)}"
    if best is not None and best["code"] != code and best["mom"] - last["mom"] > MOM_GAP:
        txt = (f"明日: 切换 {disp(code)} -> {disp(best['code'])}"
               f" (动量差{best['mom']-last['mom']:+.1%} > {MOM_GAP:.0%})")
    return txt


def trigger_lines(sim, last, close_df, open_df=None):
    """持仓离场触发线缓冲(纯展示): 明日收盘触发才执行。"""
    code = last["code"]
    if code is None:
        return []
    today = pd.Timestamp(last["date"])
    px = float(close_df.loc[today, code])
    cost_d = None
    for t in reversed(sim["trades"]):
        if t["action"] in ("buy", "switch"):
            cost_d = pd.Timestamp(t["date"])
            break
    out = []
    if TRAIL and cost_d is not None:
        peak = float(close_df[code].loc[cost_d:].max())
        out.append(f"峰值回撤线 {peak*(1-TRAIL):.3f} (缓冲 {px/(peak*(1-TRAIL))-1:+.1%})")
    drop_ref = float(close_df[code].shift(DROP_N).loc[today])
    out.append(f"止跌线 {drop_ref*(1-DROP_X):.3f} (缓冲 {px/(drop_ref*(1-DROP_X))-1:+.1%})")
    ma = float(close_df[code].rolling(MA_N).mean().loc[today])
    out.append(f"MA{MA_N} {ma:.3f} (缓冲 {px/ma-1:+.1%})")
    if cost_d is not None:
        half_done = any(t["action"] == "tp_half" for t in sim["trades"]
                        if pd.Timestamp(t["date"]) >= cost_d)
        cost = float(open_df.loc[cost_d, code]) if open_df is not None else px
        if not half_done:
            out.append(f"止盈线 {cost*(1+TP_HALF):.3f} (缓冲 {px/(cost*(1+TP_HALF))-1:+.1%})")
    return out


def build_report(state, today_signal, close_df, names, bench_series):
    os.makedirs(os.path.join(REPORT_DIR, "data"), exist_ok=True)
    nav_df = pd.DataFrame(state["nav_history"])
    nav_df.to_json(NAV_FILE, orient="records")

    trades = state["trades"]
    code = today_signal["code"]
    today = state["nav_history"][-1]["date"]
    nav_now = state["nav_history"][-1]["nav"]

    def disp(c):
        return f"{names.get(c, c)} ({c})" if c else "-"

    # 今日操作文案
    last_action = trades[-1] if trades else None
    if last_action and last_action["date"] == today:
        if last_action["action"] == "sell":
            action_txt = f"今日卖出（{last_action['reason']}）→ 轮动仓空仓"
        else:
            action_txt = f"今日{last_action['action']} {disp(last_action['code'])}"
    elif code:
        action_txt = f"持有 {disp(code)}"
    else:
        action_txt = "轮动仓空仓（持币）"

    holding = (f"底仓 {BASE_W:.0%} {disp(BASE_ETF)} + "
               f"{disp(code) if code else '现金'}")
    sm = calc_metrics(nav_df.set_index("date")["nav"])
    if not sm:
        sm = {"total_return": 0.0, "annual_return": 0.0,
              "max_drawdown": 0.0, "sharpe": 0.0}

    # 报告: 回测同款整页 plotly (plot_results 内部归一化/裁剪, 与回测完全一致)
    sv = pd.Series(nav_df["nav"].values, index=pd.to_datetime(nav_df["date"]))
    trades_ts = [dict(t, date=pd.Timestamp(t["date"])) for t in trades]
    bix = bench_series["纳指"].dropna()
    nav_dates = pd.to_datetime(nav_df["date"])
    bix = bix[bix.index >= pd.Timestamp(state["start"])].reindex(
        nav_dates, method="ffill").dropna()
    bench_ret = (bix.iloc[-1] / bix.iloc[0] - 1) * 100
    title_text = (f"v7 行业ETF信号调仓 · 模拟盘 ({state['start']} ~ {today})<br><sup>"
                  f"当前: {holding} | {action_txt} | 净值 {nav_now:.4f} | "
                  f"自起始 {sm['total_return']:+.1f}% 年化 {sm['annual_return']:+.1f}% | "
                  f"夏普 {sm['sharpe']:.2f} 回撤 {sm['max_drawdown']:.1f}% | "
                  f"纳指自起始 {bench_ret:+.1f}% | MA{MA_N} 动量{LOOKBACK}日 "
                  f"动量差&gt;{MOM_GAP*100:.0f}% 峰值止损{TRAIL*100:.0f}% "
                  f"止跌{DROP_N}d{DROP_X:.0%} "
                  f"止盈浮盈{TP_HALF*100:.0f}%卖{TP_FRAC*100:.0f}% 冷却{COOLDOWN}日</sup>")
    html = plot_results(sv, bench_series, trades_ts, names, state["start"],
                        title_text=title_text)
    html = inject_rank(html, today_signal, names)
    with open(os.path.join(REPORT_DIR, "index.html"), "w", encoding="utf-8") as f:
        f.write(html)

    ACTION_LABEL = {"buy": "买入", "sell": "卖出", "switch": "切换"}
    trades_since = [
        {"date": t["date"], "label": ACTION_LABEL.get(t["action"], t["action"]),
         "kind": t["action"], "name": disp(t["code"]),
         "reason": t["reason"]}
        for t in trades if t["date"] >= state["start"]
    ]

    lines = ["## v7 模拟盘状态"]
    lines += [f"- 日期: {today}", f"- 当前持仓: {holding}", f"- 今日操作: {action_txt}",
              f"- 明日操作: {today_signal['next_txt']}",
              f"- 自起始({state['start']})收益: {sm['total_return']:+.1f}% 年化: {sm['annual_return']:+.1f}%",
              f"- 夏普: {sm['sharpe']:.2f} 最大回撤: {sm['max_drawdown']:.1f}%",
              f"- 纳指自起始: {bench_ret:+.1f}%"]
    if trades_since:
        lines += ["", "### 交易记录", "| 日期 | 操作 | 标的 | 原因 |", "|---|---|---|---|"]
        lines += [f"| {t['date']} | {t['label']} | {t['name']} | {t['reason']} |" for t in trades_since]
    lines += ["", "### 动量排名 TOP5"]
    lines += [f"{i}. {names.get(r['code'], r['code'])} ({r['code']}) "
              f"动量{r['mom']*100:+.1f}%{' [持仓]' if r['holding'] else (' [冷却]' if r['cool'] else '')}"
              for i, r in enumerate(today_signal["rank"][:5], 1)]
    if today_signal["triggers"]:
        lines += ["", "### 持仓触发线 (明日收盘触发才执行)"]
        lines += [f"- {t}" for t in today_signal["triggers"]]
    return "\n".join(lines)


def inject_rank(html, today_signal, names):
    """往 plotly 报告页尾注入 明日操作 + 动量排名表。"""
    disp = lambda c: f"{names.get(c, c)} ({c})"
    trig = " | ".join(today_signal["triggers"]) or "无持仓"
    rank_rows = "".join(
        f"<tr><td>{i}</td><td>{disp(r['code'])}</td>"
        f"<td>{r['mom']*100:+.1f}%</td>"
        f"<td>{'持仓' if r['holding'] else ('冷却' if r['cool'] else '')}</td></tr>"
        for i, r in enumerate(today_signal["rank"], 1))
    extra = f"""
<div style="font-family:sans-serif;padding:0 12px;max-width:1100px;margin:0 auto">
  <div style="background:#fffbe6;border:1px solid #e6c200;border-radius:6px;padding:10px 14px;margin-top:14px">
    <b>明日操作</b> {today_signal['next_txt']}<br>
    <span style="color:#666;font-size:13px">触发线(明日收盘触发才执行): {trig}</span>
  </div>
  <h3 style="margin:18px 0 6px">动量排名 TOP10 (180日动量)</h3>
  <table style="border-collapse:collapse;font-family:sans-serif;font-size:14px">
    <tr style="background:#f0f0f0"><th style="padding:4px 10px;border:1px solid #ddd">#</th>
      <th style="padding:4px 10px;border:1px solid #ddd">标的</th>
      <th style="padding:4px 10px;border:1px solid #ddd">动量</th>
      <th style="padding:4px 10px;border:1px solid #ddd">状态</th></tr>
    {rank_rows}
  </table>
</div>
</body>"""
    return html.replace("</body>", extra)


def main():
    update_bench()
    close_df, open_df, names, tradable = load_close_df()
    bench_series = {}
    for name, path in BENCH_CACHES.items():
        d = json.load(open(path))
        bench_series[name] = pd.Series(d["close"], index=pd.to_datetime(d["dates"])).sort_index()
    print(f"数据: {len(close_df)} 个交易日, {len(close_df.columns)} 只")
    state = load_state()
    state, today_signal = simulate(state, close_df, open_df=open_df, tradable=tradable, names=names)
    save_state(state)
    report = build_report(state, today_signal, close_df, names, bench_series)
    print(report)
    print(f"\n报告 -> {os.path.join(REPORT_DIR, 'index.html')}")


if __name__ == "__main__":
    main()
