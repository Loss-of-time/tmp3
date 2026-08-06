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
                      BASE_ETF, rotation_sim)
from paper_trade import incremental_fetch
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


HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>v7 行业ETF信号调仓模拟盘</title>
<script src="https://cdn.jsdelivr.net/npm/echarts@5.5.1/dist/echarts.min.js"></script>
<style>
  :root{--bg:#0f172a;--card:#1e293b;--line:#334155;--txt:#e2e8f0;--mut:#94a3b8;
        --acc:#636efa;--up:#34d399;--down:#f87171}
  *{box-sizing:border-box}
  body{margin:0;font-family:-apple-system,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif;
       background:var(--bg);color:var(--txt)}
  .wrap{max-width:1080px;margin:0 auto;padding:28px 20px 60px}
  h1{font-size:22px;margin:0 0 2px;letter-spacing:.5px}
  .sub{color:var(--mut);font-size:13px;margin:0 0 6px;line-height:1.6}
  .tag{display:inline-block;padding:2px 10px;border-radius:999px;font-size:12px;
       background:rgba(99,102,241,.15);color:#a5b4fc;margin-top:4px}
  .cards{display:grid;grid-template-columns:repeat(6,1fr);gap:10px;margin:20px 0}
  .card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px}
  .card span{display:block;color:var(--mut);font-size:11px;margin-bottom:6px}
  .card b{font-size:18px}
  .card small{color:var(--mut);font-size:11px}
  .pos{color:var(--up)}.neg{color:var(--down)}
  .panel{background:var(--card);border:1px solid var(--line);border-radius:12px;
         padding:16px 18px;margin-bottom:20px}
  .panel h3{margin:0 0 12px;font-size:14px;color:var(--mut);font-weight:600}
  #chart{width:100%;height:480px}
  .grid2{display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-bottom:20px}
  @media(max-width:800px){.cards{grid-template-columns:repeat(3,1fr)}.grid2{grid-template-columns:1fr}}
  .holding-big{font-size:26px;font-weight:700;margin:4px 0 10px}
  .mom-row{display:flex;justify-content:space-between;padding:7px 0;border-bottom:1px solid #263249;font-size:13px}
  .mom-row:last-child{border-bottom:none}
  .mom-row .nm{color:var(--txt)}
  .mom-row .val{font-family:ui-monospace,Menlo,monospace}
  .pill{padding:1px 8px;border-radius:999px;font-size:11px;margin-left:8px}
  .pill.hold{background:rgba(52,211,153,.15);color:var(--up)}
  .pill.cool{background:rgba(248,113,113,.15);color:var(--down)}
  .empty{color:var(--mut);font-size:13px;padding:20px 0;text-align:center}
  table{width:100%;border-collapse:collapse;font-size:12.5px}
  th,td{padding:8px 10px;text-align:left;border-bottom:1px solid #263249}
  th{color:var(--mut);font-weight:600}
  .badge{padding:2px 8px;border-radius:999px;font-size:11px;color:#0f172a;font-weight:600}
  .badge.buy{background:var(--up)}.badge.sell{background:var(--down)}
  .badge.switch{background:#93c5fd}.badge.hold{background:#c4b5fd}
</style>
</head>
<body>
<div class="wrap">
  <h1>v7 行业ETF信号调仓 · 模拟盘</h1>
  <p class="sub" id="sub"></p>
  <span class="tag" id="rule"></span>
  <div class="cards">
    <div class="card"><span>当前净值</span><b id="nav"></b></div>
    <div class="card"><span>自起始收益</span><b id="ret"></b></div>
    <div class="card"><span>年化</span><b id="ann"></b></div>
    <div class="card"><span>最大回撤</span><b id="dd"></b></div>
    <div class="card"><span>夏普</span><b id="sharpe"></b></div>
    <div class="card"><span>纳指自起始</span><b id="bench"></b></div>
  </div>
  <div class="panel"><h3>策略净值 vs 纳指/上证</h3><div id="chart"></div></div>
  <div class="grid2">
    <div class="panel">
      <h3>今日信号</h3>
      <div id="holding"></div>
      <div id="signals"></div>
    </div>
    <div class="panel">
      <h3>动量排名（今日可买候选）</h3>
      <div id="ranking"></div>
    </div>
  </div>
  <div class="panel"><h3>交易记录</h3>
    <table><thead><tr><th>日期</th><th>操作</th><th>标的</th><th>原因</th></tr></thead>
    <tbody id="trades"></tbody></table>
  </div>
</div>
<script id="data" type="application/json">__DATA__</script>
<script>
const D = JSON.parse(document.getElementById('data').textContent);
const $ = id => document.getElementById(id);
$('sub').textContent = D.meta.sub;
$('rule').textContent = D.meta.rule;
$('nav').textContent = D.meta.nav;
$('ret').textContent = D.meta.ret;
$('ann').textContent = D.meta.ann;
$('dd').textContent = D.meta.dd;
$('sharpe').textContent = D.meta.sharpe;
$('bench').textContent = D.meta.bench;

const fmt = (x, cls) => `<b class="${cls}">${x}</b>`;
const hd = D.holding;
$('holding').innerHTML = hd.empty
  ? '<div class="empty">轮动仓空仓（持币 ' + (hd.cash_pct*100).toFixed(0) + '%）</div>'
  : `<div class="holding-big">${hd.name}</div>
     <div class="mom-row"><span class="nm">持仓</span><span class="val ${hd.mom>0?'pos':'neg'}">动量 ${(hd.mom*100).toFixed(1)}%</span></div>
     <div class="mom-row"><span class="nm">持有天数</span><span class="val">${hd.days}</span></div>`;
$('signals').innerHTML = D.signals.map(s =>
  `<div class="mom-row"><span class="nm">${s.txt}${s.pill}</span><span class="val"></span></div>`).join('')
  || '<div class="empty">-</div>';

$('ranking').innerHTML = D.ranking.length
  ? D.ranking.map((r,i) =>
    `<div class="mom-row"><span class="nm">${i+1}. ${r.name}${r.hold?'<span class="pill hold">持仓</span>':''}${r.cool?'<span class="pill cool">冷却</span>':''}</span>
     <span class="val ${r.mom>0?'pos':'neg'}">${(r.mom*100).toFixed(1)}%</span></div>`).join('')
  : '<div class="empty">今日无站上牛熊线的可买标的</div>';

const tb = $('trades');
if (!D.trades.length) { tb.innerHTML = '<tr><td colspan="4" class="empty">暂无交易</td></tr>'; }
else for (const t of D.trades) {
  const tr = document.createElement('tr');
  tr.innerHTML = `<td>${t.date}</td><td><span class="badge ${t.kind}">${t.label}</span></td><td>${t.name}</td><td>${t.reason}</td>`;
  tb.appendChild(tr);
}

const chart = echarts.init($('chart'));
const mkPoint = (d, v, sym, color) => ({coord:[d,v], symbol:sym, symbolSize:11,
  itemStyle:{color, borderColor:'#0f172a', borderWidth:1.5},
  label:{show:true, fontSize:10, color, position:'top', distance:6}});
const marks = [];
for (const t of D.trades) {
  const di = D.dates.indexOf(t.date);
  if (di < 0) continue;
  const nv = D.navs[di];
  if (t.kind === 'buy' || t.kind === 'switch') marks.push(mkPoint(t.date, nv, 'triangle', '#34d399'));
  else if (t.kind === 'sell') marks.push(mkPoint(t.date, nv, 'pin', '#f87171'));
}
chart.setOption({
  tooltip: {trigger:'axis', axisPointer:{type:'cross'}, backgroundColor:'#1e293b',
            borderColor:'#334155', textStyle:{color:'#e2e8f0', fontSize:12}},
  legend: {top:0, textStyle:{color:'#94a3b8', fontSize:12}, type:'scroll'},
  grid: [
    {left:56, right:24, top:36, height:'58%'},
    {left:56, right:24, top:'74%', height:'15%'}
  ],
  xAxis: [
    {type:'category', data:D.dates, boundaryGap:false, gridIndex:0,
     axisLabel:{fontSize:11, color:'#94a3b8'}, axisLine:{lineStyle:{color:'#334155'}}},
    {type:'category', data:D.dates, gridIndex:1, show:false}
  ],
  yAxis: [
    {gridIndex:0, scale:true, name:'净值', nameTextStyle:{color:'#94a3b8'},
     axisLabel:{fontSize:11, color:'#94a3b8'}, splitLine:{lineStyle:{color:'#263249'}}},
    {gridIndex:1, show:false, max:1.2}
  ],
  series: [
    {name:'策略净值', type:'line', data:D.navs, smooth:true, showSymbol:false,
     lineStyle:{width:2.5, color:'#636efa'},
     areaStyle:{color:'#636efa', opacity:0.10},
     markPoint:{data:marks}},
    {name:'纳指', type:'line', data:D.benchs.ixic, smooth:true, showSymbol:false,
     lineStyle:{width:1.5, color:'#f59e0b', dash:[5,4]}},
    {name:'上证', type:'line', data:D.benchs.sse, smooth:true, showSymbol:false,
     lineStyle:{width:1.5, color:'#22d3ee', dash:[5,4]}},
    ...D.segs.map(s => ({
      name:s.name, type:'bar', stack:'rot', data:s.data,
      xAxisIndex:1, yAxisIndex:1, barWidth:'70%',
      itemStyle:{color:s.color, borderRadius:[4,4,4,4]},
      emphasis:{itemStyle:{color:s.color}},
      showBackground:true, backgroundStyle:{color:'rgba(255,255,255,0.03)'}
    }))
  ]
});
chart.on('legendselectchanged', () => {});
</script>
</body>
</html>
"""


def _cand_file(code):
    for d in dynpool.DIRS:
        f = os.path.join(d, f"{code}.json")
        if os.path.exists(f):
            return f
    return None


def load_close_df():
    """读候选缓存, 最新日期早于今天则增量更新, 构建动态池。返回 (close_df, open_df, names)。"""
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
    close_df, open_df, _ = dynpool.build_pool(cands, dynpool.fetch_aum())
    return close_df, open_df, names


def load_state():
    if os.path.exists(STATE_FILE):
        return json.load(open(STATE_FILE))
    return None


def save_state(state):
    with open(STATE_FILE, "w") as fh:
        json.dump(state, fh, ensure_ascii=False, indent=2)


def simulate(state, close_df, open_df=None):
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
                       shift_full=True)
    state["nav_history"] = [{"date": str(d.date()), "nav": round(n, 4)}
                            for d, n in zip(sim["dates"], sim["navs"])]
    state["trades"] = [dict(t, date=str(t["date"].date())) for t in sim["trades"]]

    last = sim["last"]
    today_signal = {
        "code": last["code"], "name": None if last["code"] is None else "",
        "mom": last["mom"], "days": last["days"], "cash_pct": last["cash_pct"],
        "cooldowns": last["cooldowns"], "candidates": last["candidates"],
    }
    return state, today_signal


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

    # 基准归一化到起始日: bench_series = {名称: Series}
    common = nav_df["date"].tolist()
    benchs = {}
    for name, s in bench_series.items():
        hs = s.dropna().reindex(pd.to_datetime(common), method="ffill")
        benchs[name] = (hs / hs.iloc[0]).round(4).tolist() if len(hs) else []

    # 持仓区间
    dates = common
    n = len(dates)
    segments = []
    open_seg = None
    for t in trades:
        if t["action"] in ("buy", "switch") and t["code"]:
            open_seg = {"code": t["code"], "name": disp(t["code"]),
                        "start": t["date"]}
        elif t["action"] == "sell" and open_seg:
            segments.append((open_seg, t["date"]))
            open_seg = None
    if open_seg:
        segments.append((open_seg, today))
    palette = ["#636efa", "#ef553b", "#00cc96", "#ab63fa", "#ffa15a",
               "#19d3f3", "#ff6692", "#b6e880", "#ff97ff", "#fecb52",
               "#22d3ee", "#f472b6"]
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

    # 今日信号面板
    if today_signal and today_signal["code"]:
        hd = {"empty": False, "name": disp(today_signal["code"]),
              "mom": today_signal["mom"], "days": today_signal["days"], "cash_pct": 0}
    else:
        hd = {"empty": True, "cash_pct": today_signal["cash_pct"] if today_signal else 1 - BASE_W}
    sig_txt = f"{action_txt}"
    signals = [{"txt": sig_txt, "pill": ""}]
    if today_signal and today_signal["cooldowns"]:
        for c in today_signal["cooldowns"]:
            signals.append({"txt": f"冷却中: {disp(c)}（{COOLDOWN} 天内不买回）", "pill": "cool"})

    cands = today_signal["candidates"] if today_signal else []
    ranking = []
    for c in sorted(cands, key=lambda x: -x["mom"]):
        ranking.append({"name": disp(c["code"]), "mom": c["mom"],
                        "hold": c["holding"], "cool": c["cool"]})

    ACTION_LABEL = {"buy": "买入", "sell": "卖出", "switch": "切换"}
    trades_since = [
        {"date": t["date"], "label": ACTION_LABEL.get(t["action"], t["action"]),
         "kind": t["action"], "name": disp(t["code"]),
         "reason": t["reason"]}
        for t in trades if t["date"] >= state["start"]
    ]

    bench_ret = (benchs["纳指"][-1] - 1) * 100 if benchs.get("纳指") else 0.0
    data = {
        "dates": dates, "navs": [round(x, 4) for x in nav_df["nav"].tolist()],
        "benchs": {"ixic": benchs.get("纳指", []), "sse": benchs.get("上证", [])},
        "segs": segs, "trades": trades_since,
        "holding": hd, "signals": signals, "ranking": ranking,
        "meta": {
            "nav": f"{nav_now:.4f}", "ret": f"{sm['total_return']:+.1f}%",
            "ann": f"{sm['annual_return']:+.1f}%", "dd": f"-{sm['max_drawdown']:.1f}%",
            "sharpe": f"{sm['sharpe']:.2f}", "bench": f"{bench_ret:+.1f}%",
            "sub": f"起始 {state['start']} ~ {today} · 当前: {holding} · {action_txt}",
            "rule": (f"每日信号检视 | MA{MA_N}牛熊线 | 动量{LOOKBACK}日 | "
                     f"动量差&gt;{MOM_GAP*100:.0f}%才切 | 止损{TRAIL*100:.0f}% | "
                     f"冷却{COOLDOWN}日 | 底仓{BASE_W*100:.0f}%"),
        },
    }
    html = HTML_TEMPLATE.replace("__DATA__", json.dumps(data, ensure_ascii=False))
    with open(os.path.join(REPORT_DIR, "index.html"), "w", encoding="utf-8") as f:
        f.write(html)

    lines = ["## v7 模拟盘状态"]
    lines += [f"- 日期: {today}", f"- 当前持仓: {holding}", f"- 今日操作: {action_txt}",
              f"- 净值: {nav_now:.4f}",
              f"- 自起始({state['start']})收益: {sm['total_return']:+.1f}% 年化: {sm['annual_return']:+.1f}%",
              f"- 夏普: {sm['sharpe']:.2f} 最大回撤: {sm['max_drawdown']:.1f}%",
              f"- 纳指自起始: {bench_ret:+.1f}%"]
    if trades_since:
        lines += ["", "### 交易记录", "| 日期 | 操作 | 标的 | 原因 |", "|---|---|---|---|"]
        lines += [f"| {t['date']} | {t['label']} | {t['name']} | {t['reason']} |" for t in trades_since]
    return "\n".join(lines)


def main():
    update_bench()
    close_df, open_df, names = load_close_df()
    bench_series = {}
    for name, path in BENCH_CACHES.items():
        d = json.load(open(path))
        bench_series[name] = pd.Series(d["close"], index=pd.to_datetime(d["dates"])).sort_index()
    print(f"数据: {len(close_df)} 个交易日, {len(close_df.columns)} 只")
    state = load_state()
    state, today_signal = simulate(state, close_df, open_df=open_df)
    save_state(state)
    report = build_report(state, today_signal, close_df, names, bench_series)
    print(report)
    print(f"\n报告 -> {os.path.join(REPORT_DIR, 'index.html')}")


if __name__ == "__main__":
    main()
