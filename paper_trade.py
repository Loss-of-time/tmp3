#!/usr/bin/env python3
"""v5 行业ETF轮动策略模拟盘 (paper trading)。

每日增量更新行情缓存 -> 按策略规则推进模拟账户状态 -> 输出当前持仓/今日信号/
净值曲线报告 (docs/index.html, 供 GitHub Pages 展示)。

模拟账户从首次运行的日期开始: BASE_W 底仓恒持红利低波, 剩余资金做板块轮动,
规则与 etf_rotation.py 回测完全一致 (MA_N 牛熊线 / LOOKBACK 动量 / REBAL 调仓 /
TRAIL 移动止损)。状态持久化到 paper_state.json, 净值历史存 docs/data/nav.json。
"""

import json
import os
import time
from datetime import datetime, date

import numpy as np
import pandas as pd

from backtest import calc_metrics
from etf_rotation import (
    MA_N, LOOKBACK, REBAL, TRAIL, BASE_W, BASE_ETF, CASH_APR,
    COMMISSION, ETFS_DIR,
)
from fetch_etf_industry import ETFS, NEW_ETFS, LOWVOL

STATE_FILE = "paper_state.json"
REPORT_DIR = "docs"
NAV_FILE = os.path.join(REPORT_DIR, "data", "nav.json")

ALL_ETFS = {**ETFS, **NEW_ETFS, **LOWVOL}


HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>行业ETF轮动策略模拟盘</title>
<script src="https://cdn.jsdelivr.net/npm/echarts@5.5.1/dist/echarts.min.js"></script>
<style>
  body{margin:0;font-family:-apple-system,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif;
       background:#f5f7fa;color:#1e293b}
  .wrap{max-width:960px;margin:0 auto;padding:24px 16px 48px}
  h1{font-size:22px;margin:0 0 4px}
  .sub{color:#64748b;font-size:13px;margin:0 0 4px;line-height:1.6}
  .metrics{color:#94a3b8;font-size:12px;margin:0 0 20px}
  .cards{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin-bottom:20px}
  .card{background:#fff;border:1px solid #e2e8f0;border-radius:10px;padding:14px 16px}
  .card span{display:block;color:#94a3b8;font-size:12px;margin-bottom:6px}
  .card b{font-size:15px}
  #chart{background:#fff;border:1px solid #e2e8f0;border-radius:10px;margin-bottom:28px}
  h2{font-size:16px;margin:0 0 12px}
  table{width:100%;border-collapse:collapse;background:#fff;border:1px solid #e2e8f0;
        border-radius:10px;overflow:hidden;font-size:13px}
  th,td{padding:9px 12px;text-align:left;border-bottom:1px solid #f1f5f9}
  th{background:#f8fafc;color:#64748b;font-weight:600}
  .badge{padding:2px 8px;border-radius:999px;font-size:12px;color:#fff}
  .badge.buy{background:#10b981}.badge.sell{background:#ef4444}.badge.switch{background:#3b82f6}
</style>
</head>
<body>
<div class="wrap">
  <h1>行业ETF轮动策略模拟盘</h1>
  <p class="sub" id="sub"></p>
  <p class="metrics" id="metrics"></p>
  <div class="cards">
    <div class="card"><span>当前持仓</span><b id="holding"></b></div>
    <div class="card"><span>今日操作</span><b id="action"></b></div>
    <div class="card"><span>净值</span><b id="nav"></b></div>
    <div class="card"><span>自起始收益</span><b id="ret"></b></div>
  </div>
  <div id="chart" style="height:520px;"></div>
  <h2>交易记录</h2>
  <table><thead><tr><th>日期</th><th>操作</th><th>标的</th><th>原因</th></tr></thead>
  <tbody id="trades"></tbody></table>
</div>
<script id="data" type="application/json">__DATA__</script>
<script>
const D = JSON.parse(document.getElementById('data').textContent);
document.getElementById('sub').textContent = D.meta.sub;
document.getElementById('metrics').textContent = D.meta.metrics;
document.getElementById('holding').textContent = D.meta.holding;
document.getElementById('action').textContent = D.meta.action;
document.getElementById('nav').textContent = D.meta.nav;
document.getElementById('ret').textContent = D.meta.ret;

const tb = document.getElementById('trades');
for (const t of D.trades) {
  const tr = document.createElement('tr');
  tr.innerHTML = `<td>${t.date}</td><td><span class="badge ${t.kind}">${t.label}</span></td><td>${t.name}</td><td>${t.reason}</td>`;
  tb.appendChild(tr);
}

const chart = echarts.init(document.getElementById('chart'));
chart.setOption({
  tooltip: {trigger: 'axis', axisPointer: {type: 'line'}},
  legend: {top: 0, textStyle: {fontSize: 12}, type: 'scroll'},
  grid: [
    {left: 56, right: 20, top: 40, height: '50%'},
    {left: 56, right: 20, top: '77%', height: '16%'}
  ],
  xAxis: [
    {type: 'category', data: D.dates, boundaryGap: false, gridIndex: 0,
     axisLabel: {fontSize: 11}, axisLine: {lineStyle: {color: '#cbd5e1'}}},
    {type: 'category', data: D.dates, gridIndex: 1, show: false}
  ],
  yAxis: [
    {gridIndex: 0, scale: true, name: '净值', nameTextStyle: {color: '#94a3b8'},
     axisLabel: {fontSize: 11}, splitLine: {lineStyle: {color: '#eef2f7'}}},
    {gridIndex: 1, show: false, max: 1.2}
  ],
  series: [
    {name: '净值', type: 'line', data: D.navs, smooth: true, showSymbol: false,
     lineStyle: {width: 2.5, color: '#636efa'}, areaStyle: {color: '#636efa', opacity: 0.12}},
    ...D.segs.map(s => ({
      name: s.name, type: 'bar', stack: 'rot', data: s.data,
      xAxisIndex: 1, yAxisIndex: 1, barWidth: '70%',
      itemStyle: {color: s.color, borderRadius: [4, 4, 4, 4]},
      emphasis: {itemStyle: {color: s.color}}
    }))
  ]
});
</script>
</body>
</html>
"""


def load_close_df():
    """读本地缓存, 若最新日期早于今天则增量更新。返回 (close_df, names)。"""
    close = {}
    names = {}
    need_update = []
    for code, name in ALL_ETFS.items():
        f = os.path.join(ETFS_DIR, f"{code}.json")
        if not os.path.exists(f):
            need_update.append(code)
            continue
        d = json.load(open(f))
        close[code] = pd.Series(d["close"], index=pd.to_datetime(d["dates"]))
        names[code] = d.get("name", name)
        if pd.Timestamp(d["dates"][-1]) < pd.Timestamp(date.today()):
            need_update.append(code)
    if need_update:
        print(f"增量更新 {len(need_update)} 只: {[ALL_ETFS[c] for c in need_update]}")
        for code in need_update:
            try:
                incremental_fetch(code, ALL_ETFS[code])
                d = json.load(open(os.path.join(ETFS_DIR, f"{code}.json")))
                close[code] = pd.Series(d["close"], index=pd.to_datetime(d["dates"]))
                names[code] = d.get("name", ALL_ETFS[code])
            except Exception as e:
                print(f"  {code} 更新失败, 用缓存: {str(e)[:80]}")
    close_df = pd.DataFrame(close).sort_index()
    return close_df[~close_df.index.duplicated()].ffill(), names


def incremental_fetch(code, name, retries=12, pause=15):
    """从缓存最后日期往前推 10 天重新拉取, 覆盖重叠区后追加, 保证 qfq 拼接一致。

    数据源: 腾讯 fqkline (东财 push2his 2026-08 起不可用)。
    """
    import urllib.request

    from fetch_etf_industry import _tencent_page

    f = os.path.join(ETFS_DIR, f"{code}.json")
    d = json.load(open(f))
    last = d["dates"][-1]
    start = (pd.Timestamp(last) - pd.Timedelta(days=10)).strftime("%Y-%m-%d")
    end = date.today().strftime("%Y-%m-%d")
    for attempt in range(retries):
        try:
            pref = ("sz" if code.startswith("1") else "sh") + code
            url = (f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
                   f"?param={pref},day,{start},{end},640,qfq")
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            j = json.load(urllib.request.urlopen(req, timeout=15))
            day = j["data"][pref].get("day") or j["data"][pref].get("qfqday")
            new_dates = [r[0] for r in day]
            new_open = [round(float(r[1]), 4) for r in day]
            new_close = [round(float(r[2]), 4) for r in day]
            keep_mask = [ts > last for ts in new_dates]
            merged_dates = d["dates"] + [x for x, k in zip(new_dates, keep_mask) if k]
            merged_open = d.get("open", []) + [x for x, k in zip(new_open, keep_mask) if k]
            merged_close = d["close"] + [x for x, k in zip(new_close, keep_mask) if k]
            d["dates"], d["close"] = merged_dates, merged_close
            if len(merged_open) == len(merged_dates):
                d["open"] = merged_open
            with open(f, "w") as fh:
                json.dump(d, fh, ensure_ascii=False)
            added = sum(keep_mask)
            print(f"  {code} {name}: +{added} 行 (至 {merged_dates[-1]})")
            return
        except Exception as e:
            if attempt == retries - 1:
                raise
            print(f"  {code} 重试({attempt+1}): {str(e)[:60]}")
            time.sleep(pause)


def load_state():
    if os.path.exists(STATE_FILE):
        return json.load(open(STATE_FILE))
    return None


def save_state(state):
    with open(STATE_FILE, "w") as fh:
        json.dump(state, fh, ensure_ascii=False, indent=2)


def simulate(state, close_df):
    """从模拟账户起始日完整重放至最新交易日。幂等：每次运行都从 start 重放,
    状态只存起始日+当前账户, 不依赖数据框行数 (池子扩容后 i 定位会失效)。"""
    bt = close_df
    ma = bt.rolling(MA_N).mean()
    above = bt > ma
    mom = bt.pct_change(LOOKBACK)
    lowvol = bt[BASE_ETF]

    if state is None:
        start_date = bt.index[-1]
        state = {
            "start": str(start_date.date()),
            "base_shares": BASE_W / lowvol.iloc[-1],
            "rot_cash": 1.0 - BASE_W,
            "code": None, "shares": 0.0, "peak": 0.0,
            "nav_history": [], "trades": [],
        }
        start_i = len(bt) - 1
    else:
        start_i = bt.index.get_indexer([pd.Timestamp(state["start"])])[0]
        state["base_shares"] = BASE_W / lowvol.iloc[start_i]
        state["rot_cash"] = 1.0 - BASE_W
        state["code"] = None
        state["shares"] = 0.0
        state["peak"] = 0.0
        state["nav_history"] = []
        state["trades"] = []

    for i in range(start_i, len(bt)):
        date = bt.index[i]
        signal = above.loc[date]
        code = state["code"]
        if code is not None:
            px = close_df.loc[date, code]
            state["peak"] = max(state["peak"], px)
            reason = None
            if not bool(signal[code]):
                reason = "trend"
            elif px <= state["peak"] * (1 - TRAIL):
                reason = "trail"
            if reason:
                state["rot_cash"] += state["shares"] * px * (1 - COMMISSION)
                state["shares"] = 0.0
                state["code"] = None
                state["peak"] = 0.0
                state["trades"].append({"date": str(date.date()), "action": "sell",
                                        "code": None, "reason": reason})
        if (i - start_i) % REBAL == 0:
            elig = [c for c in signal[signal].index if not np.isnan(mom.loc[date, c])]
            if len(elig) > 0:
                best = mom.loc[date, elig].idxmax()
                best_px = close_df.loc[date, best]
                if state["code"] is None:
                    target = state["rot_cash"]
                    state["shares"] = target / best_px
                    state["rot_cash"] -= target + target * COMMISSION
                    state["code"] = best
                    state["peak"] = best_px
                    state["trades"].append({"date": str(date.date()), "action": "buy",
                                            "code": best, "reason": "new"})
                elif best != state["code"]:
                    old = state["code"]
                    px = close_df.loc[date, old]
                    state["rot_cash"] += state["shares"] * px * (1 - COMMISSION)
                    state["shares"] = 0.0
                    target = state["rot_cash"]
                    state["shares"] = target / best_px
                    state["rot_cash"] -= target + target * COMMISSION
                    state["code"] = best
                    state["peak"] = best_px
                    state["trades"].append({"date": str(date.date()), "action": "switch",
                                            "code": best, "reason": f"换 {old}"})
        if state["code"]:
            nav = (state["base_shares"] * lowvol.iloc[i]
                   + state["rot_cash"]
                   + state["shares"] * close_df.loc[date, state["code"]])
        else:
            nav = state["base_shares"] * lowvol.iloc[i] + state["rot_cash"]
        state["nav_history"].append({"date": str(date.date()), "nav": round(nav, 4)})
    return state


def build_report(state, close_df, names):
    os.makedirs(os.path.join(REPORT_DIR, "data"), exist_ok=True)
    nav_df = pd.DataFrame(state["nav_history"])
    nav_df.to_json(NAV_FILE, orient="records")

    trades = state["trades"]
    code = state["code"]
    last_action = trades[-1] if trades else None
    today = state["nav_history"][-1]["date"]
    nav_now = state["nav_history"][-1]["nav"]

    if last_action and last_action["date"] == today:
        if last_action["action"] == "sell":
            action_txt = f"今日卖出（{last_action['reason']}）→ 轮动仓空仓"
        else:
            action_txt = f"今日{last_action['action']} {names.get(last_action['code'], last_action['code'])}"
    elif code:
        action_txt = f"持有 {names.get(code, code)}"
    else:
        action_txt = "轮动仓空仓（持币）"

    holding = f"底仓 {BASE_W:.0%} {names[BASE_ETF]} + " + (f"轮动 {names.get(code, code)}" if code else "现金")
    sm = calc_metrics(nav_df.set_index("date")["nav"])

    # 轮动仓持仓区间: 由交易记录推出每段买->卖
    dates = nav_df["date"].tolist()
    n = len(dates)
    segments = []
    open_seg = None
    for t in trades:
        if t["action"] in ("buy", "switch") and t["code"]:
            open_seg = {"code": t["code"], "name": names.get(t["code"], t["code"]),
                        "start": t["date"]}
        elif t["action"] == "sell" and open_seg:
            segments.append((open_seg, t["date"]))
            open_seg = None
    if open_seg:
        segments.append((open_seg, today))
    palette = ["#636efa", "#ef553b", "#00cc96", "#ab63fa", "#ffa15a",
               "#19d3f3", "#ff6692", "#b6e880", "#ff97ff", "#fecb52"]
    code_color = {}
    for i, c in enumerate(sorted({s["code"] for s, _ in segments})):
        code_color[c] = palette[i % len(palette)]
    segs = []
    for s, end in segments:
        data = [0] * n
        i0, i1 = dates.index(s["start"]), dates.index(end)
        for i in range(i0, i1 + 1):
            data[i] = 1
        segs.append({"name": s["name"], "color": code_color[s["code"]], "data": data})

    ACTION_LABEL = {"buy": "买入", "sell": "卖出", "switch": "切换"}
    trades_since = [
        {"date": t["date"], "label": ACTION_LABEL.get(t["action"], t["action"]),
         "kind": t["action"], "name": names.get(t["code"], "-") if t["code"] else "-",
         "reason": t["reason"]}
        for t in trades if t["date"] >= state["start"]
    ]
    metrics_txt = (f"自起始收益 {sm['total_return']:.1f}% | 年化 {sm['annual_return']:.1f}% | "
                   f"夏普 {sm['sharpe']:.2f} | 最大回撤 {sm['max_drawdown']:.1f}%") if sm else "-"
    data = {
        "dates": dates, "navs": [round(x, 4) for x in nav_df["nav"].tolist()], "segs": segs,
        "trades": trades_since,
        "meta": {
            "holding": holding, "action": action_txt, "nav": f"{nav_now:.4f}",
            "ret": f"{sm['total_return']:.1f}%" if sm else "-",
            "sub": (f"底仓 {BASE_W:.0%} {names[BASE_ETF]} | 轮动 MA{MA_N}/动量{LOOKBACK}/每{REBAL}日/止损{TRAIL:.0%} | "
                    f"起始 {state['start']} ~ {today}"),
            "metrics": metrics_txt,
        },
    }
    html = HTML_TEMPLATE.replace("__DATA__", json.dumps(data, ensure_ascii=False))
    with open(os.path.join(REPORT_DIR, "index.html"), "w", encoding="utf-8") as f:
        f.write(html)

    summary_lines = []
    summary_lines.append("## 模拟盘状态")
    summary_lines.append(f"- 日期: {today}")
    summary_lines.append(f"- 当前持仓: {holding}")
    summary_lines.append(f"- 今日操作: {action_txt}")
    summary_lines.append(f"- 净值: {nav_now:.4f}")
    if sm and sm.get("total_return") is not None:
        summary_lines.append(f"- 自起始({state['start']})收益: {sm['total_return']:.1f}% 年化: {sm['annual_return']:.1f}%")
        summary_lines.append(f"- 夏普: {sm['sharpe']:.2f}  最大回撤: {sm['max_drawdown']:.1f}%")
    if trades_since:
        summary_lines.append("")
        summary_lines.append("### 交易记录")
        summary_lines.append("| 日期 | 操作 | 标的 | 原因 |")
        summary_lines.append("|---|---|---|---|")
        for t in trades_since:
            summary_lines.append(f"| {t['date']} | {t['label']} | {t['name']} | {t['reason']} |")
    return "\n".join(summary_lines)


def main():
    close_df, names = load_close_df()
    print(f"数据: {len(close_df)} 个交易日, {len(close_df.columns)} 只")
    state = load_state()
    state = simulate(state, close_df)
    save_state(state)
    report = build_report(state, close_df, names)
    print(report)
    print(f"\n报告 -> {os.path.join(REPORT_DIR, 'index.html')}")


if __name__ == "__main__":
    main()
