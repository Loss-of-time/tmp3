#!/usr/bin/env python3
"""行业板块 ETF 信号调仓回测 (v7 实验)。

修复 v5 的相位锚定缺陷: v5 用 `i % REBAL == 0` 相对起跑日锚定调仓, 起跑日平移
结果 2.4%~26.7% 天差地别, 26.6% 年化恰是 40 个锚点里的最优解, 期望 alpha 很小。

本版改为**每日检视 + 信号过滤** (参考 research/v5_reports/):
- 西部 CTA: 无固定调仓周期, 只按信号调仓
- 策引: 动量差超过阈值才切换 (避免微小差异频繁调仓)
- 汉斯: 动量得分 > 0 才可入场

规则:
- 每日检查轮动仓: 跌破 MA200 (趋势破坏) 或峰值回撤 TRAIL 离场
- 空仓时: 有 ETF 站上 MA200 且动量 > 0, 买动量最强一只
- 持仓时: 若存在候选动量超过持仓 MOM_GAP 以上, 切换过去 (否则续持)
- 不接飞刀: 距自身 60 日峰值回撤 >= TRAIL 的候选拦停 (等回撤收敛再入)
- 每日检视 => 无相位, 起跑日平移结果不变
- 底仓 BASE_W 恒持底仓标的(BASE_ETF), 剩余做轮动; 空仓吃现金 CASH_APR
"""

import glob
import json
import os
from datetime import datetime

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from backtest import COMMISSION, calc_metrics

# --- config ---
MA_N = 200          # 牛熊线: 只买收盘 > MA_N 的 ETF, 跌破则趋势破坏离场
LOOKBACK = 180      # 动量窗口(交易日)
MOM_GAP = 1.0       # 切换阈值: 候选动量超过持仓动量此比例(绝对差)才切换
MIN_MOM = 0.0       # 入场门槛: 动量必须 > 此值才可买入 (汉斯: 得分>0)
TRAIL = 0.20        # 移动止损: 从持仓峰值回撤此比例离场
COOLDOWN = 20       # 冷却期: 卖出后此天数内不买回同标的 (防止损后当日买回被打脸)
BASE_W = 0.45       # 低波底仓权重
BASE_ETF = "518880" # 底仓标的: "512890" 红利低波 / "513100" 纳指ETF(溢价可接受) / "518880" 黄金(长周期唯一单调稳健防御)
BT_START = "2020-01-01"
CASH_APR = 0.02

ETFS_DIR = "cache_bt/etf_industry"
HS300_CACHE = "cache_bt/hs300.json"
BENCHES = {"纳指": "cache_bt/ixic.json", "上证": "cache_bt/sh000001.json"}


def load_data():
    etfs = {}
    opens = {}
    for f in sorted(glob.glob(os.path.join(ETFS_DIR, "*.json"))):
        d = json.load(open(f))
        dates = pd.to_datetime(d["dates"])
        etfs[d["code"]] = pd.Series(d["close"], index=dates).sort_index()
        opens[d["code"]] = pd.Series(d["open"], index=dates).sort_index()
    hs300 = json.load(open(HS300_CACHE))
    hs300_s = pd.Series(hs300["close"], index=pd.to_datetime(hs300["dates"])).sort_index()
    return etfs, opens, hs300_s


def load_benches():
    """返回对比基准: {名称: pd.Series} (纳指 + 上证, 替代沪深300)。"""
    out = {}
    for name, path in BENCHES.items():
        d = json.load(open(path))
        out[name] = pd.Series(d["close"], index=pd.to_datetime(d["dates"])).sort_index()
    return out


def rotation_backtest(close_df, open_df=None, bt_start=BT_START, ma_n=MA_N, lookback=LOOKBACK,
                      mom_gap=MOM_GAP, min_mom=MIN_MOM, trail=TRAIL,
                      base_w=BASE_W, base_etf=BASE_ETF, cooldown=COOLDOWN):
    """信号调仓回测。返回 (values, trades)。

    成交模型: 今日用昨日收盘信号决策, 今日开盘价成交 (避免前视偏差)。
    open_df 为空时回退用当日收盘成交 (无开盘数据)。
    """
    bt = close_df[bt_start:]
    sig_close = bt.shift(1)              # 昨日收盘: 所有信号基于它
    ma = sig_close.rolling(ma_n).mean()
    above = sig_close > ma
    mom = sig_close.pct_change(lookback)
    lowvol = bt[base_etf]
    if open_df is not None:
        fill = open_df[bt_start:]
    else:
        fill = bt

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

        # 轮动仓每日检查 (昨日收盘信号): 趋势破坏或移动止损
        if code is not None:
            peak = max(peak, px)
            reason = None
            if not bool(signal[code]):
                reason = "trend"
            elif px <= peak * (1 - trail):
                reason = "trail"
            if reason:
                exec_px = fill.loc[date, code]
                proceeds = shares * exec_px * (1 - COMMISSION)
                rot_cash += proceeds
                last_sell[code] = i
                shares = 0.0
                code = None
                peak = 0.0
                trades.append({"date": date, "action": "sell", "code": None, "reason": reason})

        # 每日信号检视 (昨日收盘信号)
        elig = [c for c in signal[signal].index
                if not np.isnan(mom.loc[date, c]) and mom.loc[date, c] > min_mom
                and (cooldown == 0 or i - last_sell.get(c, -10**9) > cooldown)]
        # 不接飞刀: 距自身近期峰值(60日)回撤 >= TRAIL 的候选视为刚崩, 拦停
        # (实验见 etf_rot_signal_filter.py 模式 E: 25.1% vs 23.3%, 回撤24.7% vs 29.9%)
        if elig:
            peak60 = sig_close.iloc[max(0, i-60):i+1].max(axis=0)
            elig = [c for c in elig if sig_close.loc[date, c] >= peak60[c] * (1 - trail)]
        if len(elig) > 0:
            best = mom.loc[date, elig].idxmax()
            best_px = fill.loc[date, best]
            if code is None:
                target = rot_cash
                shares = target / best_px
                cost = target * COMMISSION
                rot_cash -= target + cost
                code = best
                peak = best_px
                trades.append({"date": date, "action": "buy", "code": best,
                               "reason": "new", "mom": round(float(mom.loc[date, best]) * 100, 1)})
            elif best != code:
                cur_mom = mom.loc[date, code]
                best_mom = mom.loc[date, best]
                if best_mom - cur_mom > mom_gap:
                    old_code = code
                    exec_px = fill.loc[date, old_code]
                    proceeds = shares * exec_px * (1 - COMMISSION)
                    rot_cash += proceeds
                    target = rot_cash
                    shares = target / best_px
                    cost = target * COMMISSION
                    rot_cash -= target + cost
                    code = best
                    peak = best_px
                    trades.append({"date": date, "action": "switch", "code": best,
                                   "reason": f"换 {old_code}",
                                   "mom": round(float(best_mom) * 100, 1)})

        px = bt.loc[date, code] if code else None
        mv = base_shares * lowvol.iloc[i] + rot_cash + (shares * px if code else 0)
        values.iloc[i] = mv

    return values, trades


def plot_results(sv, benchs, trades, names, timestamp, title_text=None):
    """benchs: {名称: 归一化Series}"""
    fig = make_subplots(
        rows=3, cols=2,
        subplot_titles=("净值曲线", "年度收益对比", "持仓标的", "策略指标"),
        row_heights=[0.4, 0.3, 0.3],
        specs=[[{"colspan": 2}, None], [{"colspan": 2}, None], [{}, {"type": "table"}]],
    )

    sv = sv / sv.iloc[0]
    fig.add_trace(go.Scatter(x=sv.index, y=sv, mode="lines", name="策略",
                             line=dict(color="steelblue", width=2)), row=1, col=1)
    bench_colors = ["coral", "seagreen"]
    for k, (name, bv) in enumerate(benchs.items()):
        bv = bv / bv.iloc[0]
        fig.add_trace(go.Scatter(x=bv.index, y=bv, mode="lines", name=name,
                                 line=dict(color=bench_colors[k % 2], width=1.5,
                                           dash="dash")), row=1, col=1)

    yr = sv.resample("YE").last()
    sy = yr.pct_change() * 100
    sy.iloc[0] = (yr.iloc[0] / sv.iloc[0] - 1) * 100
    years_labels = [str(d.year) for d in sy.index]
    fig.add_trace(go.Bar(x=years_labels, y=sy.values, name="策略", marker_color="steelblue"), row=2, col=1)
    for k, (name, bv) in enumerate(benchs.items()):
        byr = bv.resample("YE").last()
        by_ = byr.pct_change() * 100
        by_.iloc[0] = (byr.iloc[0] / bv.iloc[0] - 1) * 100
        fig.add_trace(go.Bar(x=years_labels, y=by_.values, name=name,
                             marker_color=bench_colors[k % 2], opacity=0.7), row=2, col=1)

    holds = []
    cur = None
    start = None
    for t in trades:
        if t["action"] in ("buy", "switch"):
            if cur is not None and start is not None:
                holds.append((cur, start, t["date"]))
            cur = t["code"]
            start = t["date"]
        elif t["action"] == "sell":
            if cur is not None and start is not None:
                holds.append((cur, start, t["date"]))
            cur = None
    if cur is not None and start is not None:
        holds.append((cur, start, sv.index[-1]))
    for code, s, e in holds:
        fig.add_trace(go.Scatter(
            x=[s, e], y=[f"{names[code]} {code}", f"{names[code]} {code}"],
            mode="lines", line=dict(width=8), name=f"{names[code]} {code}", showlegend=False,
            hovertext=[f"{names[code]} ({code}): {s.date()} ~ {e.date()}"],
        ), row=3, col=1)

    sm = calc_metrics(sv)
    n_buy = sum(1 for t in trades if t["action"] == "buy")
    n_switch = sum(1 for t in trades if t["action"] == "switch")

    bench_cols = []
    for name, bv in benchs.items():
        bm = calc_metrics(bv)
        bench_cols.append([f"{bm.get('annual_return',0):.1f}", f"{bm.get('sharpe',0):.2f}",
                           f"{bm.get('max_drawdown',0):.1f}", f"{bm.get('total_return',0):.1f}",
                           "-", "-", "-"])
    fig.add_trace(go.Table(
        header=dict(values=["指标", "策略", *[n for n, _ in benchs.items()]]),
        cells=dict(values=[
            ["年化收益 %", "夏普", "最大回撤 %", "总收益 %", "买入次数", "切换次数", "轮动仓"],
            [f"{sm.get('annual_return',0):.1f}", f"{sm.get('sharpe',0):.2f}",
             f"{sm.get('max_drawdown',0):.1f}", f"{sm.get('total_return',0):.1f}",
             f"{n_buy}", f"{n_switch}", f"{int((1-BASE_W)*100)}%"],
            *bench_cols,
        ]),
    ), row=3, col=2)

    if title_text is None:
        title_text = (f"行业ETF信号调仓回测 ({timestamp})<br><sup>"
                      f"每日检视 | MA{MA_N}+动量{LOOKBACK}日 动量差>{MOM_GAP:.0%}才切换 | "
                      f"低波底仓{int(BASE_W*100)}%+轮动{int((1-BASE_W)*100)}% 止损{int(TRAIL*100)}% | "
                      f"{BT_START} ~ 2026-07</sup>")
    fig.update_layout(
        title_text=title_text,
        height=1000,
    )
    fig.update_yaxes(title_text="净值", row=1, col=1)
    fig.update_yaxes(title_text="年度收益 (%)", row=2, col=1)
    fig.update_yaxes(title_text="持仓标的", row=3, col=1)
    return fig.to_html(include_plotlyjs="cdn", full_html=True)


def main():
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    print("[1/2] 读取缓存...")
    etfs, opens, _ = load_data()
    close_df = pd.DataFrame(etfs).sort_index().ffill()
    open_df = pd.DataFrame(opens).sort_index().reindex(close_df.index).ffill()
    names = {json.load(open(f))["code"]: json.load(open(f))["name"]
             for f in glob.glob(os.path.join(ETFS_DIR, "*.json"))}
    print(f"      {len(etfs)} 只: " + ", ".join(names[c] for c in close_df.columns))

    print(f"[2/2] 回测 (每日检视, MA{MA_N}, 动量{LOOKBACK}日, 动量差>{MOM_GAP:.0%}切换, "
          f"止损{TRAIL:.0%}, 底仓{BASE_W:.0%})...")
    values, trades = rotation_backtest(close_df, open_df=open_df)

    benches = {}
    for name, s in load_benches().items():
        s = s.dropna().reindex(values.index, method="ffill").dropna()
        common = values.dropna().index.intersection(s.index)
        benches[name] = s.reindex(common).ffill() if len(common) else None
    common = values.dropna().index
    sv = values.reindex(common).ffill()
    benches = {k: v for k, v in benches.items() if v is not None}

    sm = calc_metrics(sv)
    n_buy = sum(1 for t in trades if t["action"] == "buy")
    n_switch = sum(1 for t in trades if t["action"] == "switch")

    print("\n--- 回测结果 ---")
    print(f"      策略年化收益: {sm['annual_return']:.1f}%")
    for name, bv in benches.items():
        bm = calc_metrics(bv)
        print(f"      {name}年化收益: {bm['annual_return']:.1f}%")
    print(f"      策略夏普: {sm['sharpe']:.2f}")
    print(f"      策略最大回撤: {sm['max_drawdown']:.1f}%")
    print(f"      总收益: {sm['total_return']:.1f}%")
    print(f"      买入 {n_buy} 次, 切换 {n_switch} 次")

    csv_file = f"etf_rot_signal_result_{timestamp}.csv"
    df_out = {"date": sv.index, "strategy": sv.values}
    for name, bv in benches.items():
        df_out[name] = bv.values
    pd.DataFrame(df_out).to_csv(csv_file, index=False, encoding="utf-8-sig")
    print(f"      CSV -> {csv_file}")

    if trades:
        pd.DataFrame(trades).to_csv(f"etf_rot_signal_trades_{timestamp}.csv",
                                    index=False, encoding="utf-8-sig")
        print(f"      交易明细 -> etf_rot_signal_trades_{timestamp}.csv")

    html_file = f"etf_rot_signal_result_{timestamp}.html"
    with open(html_file, "w", encoding="utf-8") as f:
        f.write(plot_results(sv, benches, trades, names, timestamp))
    print(f"      HTML -> {html_file}")
    print("\n完成.")


if __name__ == "__main__":
    main()
