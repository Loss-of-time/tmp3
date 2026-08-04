#!/usr/bin/env python3
"""v7 信号轮动参数敏感性扫描: 22只池, 逐参数扰动看年化/夏普/回撤是否陡峭。

围绕默认参数 (MA_N=200, LOOKBACK=180, MOM_GAP=1.0, MIN_MOM=0.0, TRAIL=0.20,
COOLDOWN=20, BASE_W=0.45), 每次只动一个参数。输出 CSV + 控制台。

用法: source .venv/bin/activate && python3 -u etf_rot_signal_sweep.py
"""

from datetime import datetime

import pandas as pd

from backtest import calc_metrics
from etf_rot_signal import load_data, rotation_backtest

SWEEPS = {
    "ma_n":      [120, 150, 180, 200, 220, 250],
    "lookback":  [120, 150, 180, 210, 240],
    "mom_gap":   [0.0, 0.5, 1.0, 1.5, 2.0],
    "min_mom":   [-0.05, 0.0, 0.05, 0.10],
    "trail":     [0.10, 0.15, 0.20, 0.25, 0.30, 0.35],
    "cooldown":  [0, 10, 20, 40, 60],
    "base_w":    [0.30, 0.35, 0.40, 0.45, 0.50, 0.55, 0.60],
}


def main():
    print("[1/2] 读取缓存...")
    etfs, opens, _ = load_data()
    close_df = pd.DataFrame(etfs).sort_index().ffill()
    open_df = pd.DataFrame(opens).sort_index().reindex(close_df.index).ffill()

    print("[2/2] 扫描...")
    rows = []
    m0, t0 = rotation_backtest(close_df, open_df)
    base = calc_metrics(m0)
    print(f"[默认] 年化{base['annual_return']:.1f}% 夏普{base['sharpe']:.2f} 回撤{base['max_drawdown']:.1f}%")
    for param, vals in SWEEPS.items():
        for v in vals:
            kw = {param: v}
            try:
                sv, _ = rotation_backtest(close_df, open_df, **kw)
            except Exception as e:
                print(f"  {param}={v}: 失败 {e}")
                continue
            m = calc_metrics(sv)
            d = {p: getattr(__import__("etf_rot_signal"), p.upper()) for p in SWEEPS}
            d[param] = v
            rows.append({**d,
                         "annual": round(m["annual_return"], 2), "sharpe": round(m["sharpe"], 3),
                         "dd": round(m["max_drawdown"], 2)})
            print(f"  {param}={v:<5} 年化{m['annual_return']:6.1f}% 夏普{m['sharpe']:.2f} 回撤{m['max_drawdown']:5.1f}%")

    df = pd.DataFrame(rows)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    csv = f"etf_rot_signal_sweep_{ts}.csv"
    df.to_csv(csv, index=False, encoding="utf-8-sig")
    print(f"CSV -> {csv}")
    print("完成.")


if __name__ == "__main__":
    main()
