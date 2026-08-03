#!/usr/bin/env python3
"""v5 行业ETF轮动参数敏感性扫描: 逐参数扰动, 看年化/夏普/回撤是否陡峭变化。

围绕当前最优参数 (MA_N=200, LOOKBACK=180, REBAL=40, TRAIL=0.20, BASE_W=0.45),
每次只动一个参数, 其余固定为默认值。输出 CSV + HTML 表格。

用法: source .venv/bin/activate && python3 -u etf_rot_sweep.py
"""

from datetime import datetime

import pandas as pd
import plotly.graph_objects as go

from backtest import calc_metrics
from etf_rotation import load_data, rotation_backtest

DEFAULTS = dict(ma_n=200, lookback=180, rebal=40, trail=0.20, base_w=0.45)

# 每个参数的候选值 (当前默认值包含在内)
SWEEPS = {
    "ma_n":    [150, 180, 200, 220, 250],
    "lookback": [120, 150, 180, 210, 240],
    "rebal":   [20, 30, 40, 60, 80],
    "trail":   [0.15, 0.20, 0.25, 0.30, 0.35],
    "base_w":  [0.35, 0.40, 0.45, 0.50, 0.55, 0.60],
}

PARAM_LABELS = {
    "ma_n": "MA_N", "lookback": "LOOKBACK", "rebal": "REBAL",
    "trail": "TRAIL", "base_w": "BASE_W",
}


def main():
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    print("[1/2] 读取缓存...")
    etfs, hs300_s = load_data()
    close_df = pd.DataFrame(etfs).sort_index().ffill()

    print("[2/2] 参数敏感性扫描...")
    results = []

    for param, values in SWEEPS.items():
        for v in values:
            cfg = dict(DEFAULTS)
            cfg[param] = v
            values_s, trades = rotation_backtest(close_df, **cfg)

            hs300_r = hs300_s.reindex(values_s.index).ffill()
            common = values_s.dropna().index.intersection(hs300_r.dropna().index)
            sv = values_s.reindex(common).ffill()
            bv = hs300_r.reindex(common).ffill()

            sm = calc_metrics(sv)
            bm = calc_metrics(bv)
            n_buy = sum(1 for t in trades if t["action"] == "buy")
            n_switch = sum(1 for t in trades if t["action"] == "switch")

            is_default = (param, v) in [(p, DEFAULTS[p]) for p in SWEEPS]
            results.append({
                "param": param, "value": v, "default": is_default,
                "annual_return": sm["annual_return"],
                "sharpe": sm["sharpe"],
                "max_drawdown": sm["max_drawdown"],
                "total_return": sm["total_return"],
                "n_buy": n_buy, "n_switch": n_switch,
                "benchmark_annual": bm["annual_return"],
            })
            tag = " <- 默认" if is_default else ""
            print(f"  {PARAM_LABELS[param]}={v:g}: 年化{sm['annual_return']:5.1f}%  "
                  f"夏普{sm['sharpe']:.2f}  回撤{sm['max_drawdown']:4.1f}%  "
                  f"买入{n_buy} 切换{n_switch}{tag}")

    df = pd.DataFrame(results)

    csv_file = f"etf_rot_sweep_{timestamp}.csv"
    df.to_csv(csv_file, index=False, encoding="utf-8-sig")
    print(f"CSV -> {csv_file}")

    # HTML: 每参数一列, 显示 年化/夏普/回撤
    df_disp = df.copy()
    df_disp["metrics"] = df_disp.apply(
        lambda r: f"{r['annual_return']:.1f}% / {r['sharpe']:.2f} / {r['max_drawdown']:.1f}%",
        axis=1)
    fig = go.Figure()
    for param, values in SWEEPS.items():
        sub = df_disp[df_disp["param"] == param]
        fig.add_trace(go.Table(
            header=dict(values=[PARAM_LABELS[param], "年化 / 夏普 / 回撤", "买入", "切换"]),
            cells=dict(values=[
                [f"{v:g}{'★' if (param, v) == (param, DEFAULTS[param]) else ''}" for v in sub["value"]],
                sub["metrics"], sub["n_buy"], sub["n_switch"],
            ]),
            name=PARAM_LABELS[param],
        ))
    fig.update_layout(title=f"v5 参数敏感性扫描 ({timestamp})<br><sup>默认 "
                            f"MA{DEFAULTS['ma_n']}/LB{DEFAULTS['lookback']}/REBAL{DEFAULTS['rebal']}/"
                            f"TRAIL{DEFAULTS['trail']}/BASE{DEFAULTS['base_w']}  ★=默认值 | 2020-01 ~ 2026-07</sup>",
                      height=400 + len(SWEEPS) * 260)
    html_file = f"etf_rot_sweep_{timestamp}.html"
    fig.write_html(html_file)
    print(f"HTML -> {html_file}")
    print("\n完成.")


if __name__ == "__main__":
    main()
