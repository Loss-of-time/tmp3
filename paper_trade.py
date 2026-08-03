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
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from backtest import calc_metrics
from etf_rotation import (
    MA_N, LOOKBACK, REBAL, TRAIL, BASE_W, BASE_ETF, CASH_APR,
    COMMISSION, ETFS_DIR,
)
from fetch_etf_industry import ETFS, LOWVOL

STATE_FILE = "paper_state.json"
REPORT_DIR = "docs"
NAV_FILE = os.path.join(REPORT_DIR, "data", "nav.json")

ALL_ETFS = {**ETFS, **LOWVOL}


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
    """从缓存最后日期往前推 10 天重新拉取, 覆盖重叠区后追加, 保证 qfq 拼接一致。"""
    f = os.path.join(ETFS_DIR, f"{code}.json")
    import akshare as ak
    d = json.load(open(f))
    last = d["dates"][-1]
    start = (pd.Timestamp(last) - pd.Timedelta(days=10)).strftime("%Y%m%d")
    end = date.today().strftime("%Y%m%d")
    for attempt in range(retries):
        try:
            df = ak.fund_etf_hist_em(symbol=code, period="daily",
                                     start_date=start, end_date=end, adjust="qfq")
            new_dates = df["日期"].astype(str).tolist()
            new_close = [round(float(x), 4) for x in df["收盘"]]
            keep_mask = [ts > last for ts in new_dates]
            merged_dates = d["dates"] + [x for x, k in zip(new_dates, keep_mask) if k]
            merged_close = d["close"] + [x for x, k in zip(new_close, keep_mask) if k]
            d["dates"], d["close"] = merged_dates, merged_close
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
    """从上次处理日的下一交易日推进模拟账户。返回更新后的 state。"""
    bt = close_df
    ma = bt.rolling(MA_N).mean()
    above = bt > ma
    mom = bt.pct_change(LOOKBACK)
    lowvol = bt[BASE_ETF]

    first_run = state is None
    if first_run:
        # 模拟账户从最新交易日开始, 净值 1.0; 调仓计数器从 0 起, 首日即调仓日
        state = {
            "start": str(bt.index[-1].date()),
            "base_shares": BASE_W / lowvol.iloc[-1],
            "rot_cash": 1.0 - BASE_W,
            "code": None, "shares": 0.0, "peak": 0.0,
            "i": len(bt) - 1, "rebal": 0,
            "last_date": None,
            "nav_history": [], "trades": [],
        }
        start_i = len(bt) - 1
    else:
        start_i = state["i"] + 1

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
        if state["rebal"] % REBAL == 0:
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
        state["i"] = i
        state["last_date"] = str(date.date())
        state["rebal"] += 1
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

    fig = make_subplots(
        rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.12,
        row_heights=[0.6, 0.4],
        subplot_titles=("模拟盘净值", "轮动仓持仓标的"),
    )
    fig.add_trace(go.Scatter(x=nav_df["date"], y=nav_df["nav"], mode="lines",
                             name="策略净值", line=dict(width=2)), row=1, col=1)
    hold_df = pd.DataFrame(trades)
    if len(hold_df) > 0:
        for code_h, grp in hold_df.groupby("code"):
            if code_h is None:
                continue
            grp = grp.sort_values("date")
            for _, tr in grp.iterrows():
                fig.add_trace(go.Scatter(
                    x=[tr["date"]], y=[1], mode="markers",
                    name=names.get(code_h, code_h), showlegend=False,
                    marker=dict(size=10)), row=2, col=1)
    fig.update_layout(
        title_text=f"行业ETF轮动策略模拟盘<br><sup>"
                   f"底仓{BASE_W:.0%} {names[BASE_ETF]} | 轮动 MA{MA_N}/动量{LOOKBACK}/每{REBAL}日/止损{TRAIL:.0%} | "
                   f"起始 {state['start']} ~ {today}</sup>",
        height=700,
    )
    html = fig.to_html(include_plotlyjs="cdn", full_html=True)

    trades_since = [t for t in trades if t["date"] >= state["start"]]
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
            summary_lines.append(f"| {t['date']} | {t['action']} | {names.get(t['code'], '-') if t['code'] else '-'} | {t['reason']} |")

    with open(os.path.join(REPORT_DIR, "index.html"), "w", encoding="utf-8") as f:
        f.write(html)
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
