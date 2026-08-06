#!/usr/bin/env python3
"""v7 全仓回测 · 拉长市场版本。

池内 ETF 多数 2019 年后成立, 无法前推。本脚本用**长历史行业指数**代理
(sina 源, sina 的 399xxx 沪深指数自 2014-2015 起), 拉长回测到 2016 起,
全仓轮动 (base_w=0), 检验策略在 2015 崩盘后 / 2017 / 2018 熊市是否仍稳健。

池映射 (proxy 指数 + 有长历史的真 ETF):
  券商 sz399975 | 军工 sz399967 | 白酒 sz399997 | 银行 sz399986 |
  传媒 sz399971 | 医药 sh000933 | 有色 sh000819 | 消费 sh000932 |
  新能源车 sz399976 | 机器人 sz399806 | 半导体 sh000989 | 化工 sz399807 |
  红利 sh000015(上证红利, 代理红利ETF) | 黄金 518880 | 纳指 513100
剔除 (sina 无 2016 前连续历史): 煤炭/光伏/5G/电力/旅游/农业/恒生科技/红利低波

指数为价格指数(不含分红), 比持 ETF 略保守。代理首次拉取缓存
cache_bt/etf_long/, 之后离线。
"""

import json
import os
from datetime import datetime

import pandas as pd

from backtest import calc_metrics
from etf_rot_signal import rotation_backtest

CACHE = "cache_bt/etf_long"
BT_START = "2016-01-01"

PROXIES = {
    "sz399975": "券商", "sz399967": "军工", "sz399997": "白酒",
    "sz399986": "银行", "sz399971": "传媒", "sh000933": "医药",
    "sh000819": "有色", "sh000932": "消费", "sz399976": "新能源车",
    "sz399806": "机器人", "sh000989": "半导体", "sz399807": "化工",
    "sh000015": "红利",
}
ETF_BASE = "cache_bt/etf_industry"   # 黄金 518880 / 纳指 513100 用真 ETF


def fetch_proxies():
    import akshare as ak
    os.makedirs(CACHE, exist_ok=True)
    for code, name in PROXIES.items():
        f = os.path.join(CACHE, f"{code}.json")
        if os.path.exists(f):
            continue
        df = ak.stock_zh_index_daily(symbol=code)
        out = {"code": code, "name": name,
               "dates": [str(d)[:10] for d in df["date"]],
               "open": [round(float(x), 4) for x in df["open"]],
               "close": [round(float(x), 4) for x in df["close"]]}
        with open(f, "w") as fh:
            json.dump(out, fh, ensure_ascii=False)
        print(f"  代理 {code} {name}: {len(out['dates'])} 行")


def load():
    close, open_, names = {}, {}, {}
    for code, name in PROXIES.items():
        d = json.load(open(os.path.join(CACHE, f"{code}.json")))
        idx = pd.to_datetime(d["dates"])
        close[f"{name}"] = pd.Series(d["close"], index=idx)
        open_[f"{name}"] = pd.Series(d["open"], index=idx)
        names[f"{name}"] = f"{name}指数"
    for etf in ("518880", "513100"):
        d = json.load(open(os.path.join(ETF_BASE, f"{etf}.json")))
        idx = pd.to_datetime(d["dates"])
        close[etf] = pd.Series(d["close"], index=idx)
        open_[etf] = pd.Series(d["open"], index=idx)
        names[etf] = d["name"]
    close_df = pd.DataFrame(close).sort_index().ffill()
    open_df = pd.DataFrame(open_).sort_index().reindex(close_df.index).ffill()
    return close_df, open_df, names


def main():
    import sys
    base_w = float(sys.argv[1]) if len(sys.argv) > 1 else 0.0
    base_etf = sys.argv[2] if len(sys.argv) > 2 else "518880"
    print("[1/2] 拉取/读取代理数据...")
    fetch_proxies()
    close_df, open_df, names = load()
    print(f"      {len(close_df.columns)} 只: {', '.join(close_df.columns)}")
    print(f"      区间 {close_df.index[0].date()} ~ {close_df.index[-1].date()}")

    print(f"[2/2] 回测 (MA200/动量180/差>1.0/止损20%, {BT_START} 起, 底仓{base_w:.0%} {base_etf})...")
    sv, trades = rotation_backtest(close_df, open_df=open_df, bt_start=BT_START,
                                   base_w=base_w, base_etf=base_etf)
    sv = sv.dropna()
    sm = calc_metrics(sv)
    print(f"      年化 {sm['annual_return']:.1f}% 夏普 {sm['sharpe']:.2f} "
          f"回撤 {sm['max_drawdown']:.1f}% 总收益 {sm['total_return']:.1f}%")
    print(f"      交易 {len(trades)} 笔")

    sy = sv.resample("YE").last().pct_change().dropna() * 100
    for d, v in sy.items():
        print(f"      {d.year}: {v:+.1f}%")

    for t in trades:
        print(f"      {t['date'].date()} {t['action']:<6} "
              f"{(names.get(t['code']) if t['code'] else '-'):<12} {t.get('reason','')}")

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out = {"date": sv.index, "strategy": sv.values}
    pd.DataFrame(out).to_csv(f"etf_rot_long_result_{ts}.csv", index=False)
    print(f"CSV -> etf_rot_long_result_{ts}.csv")

    benches = {}
    for name, path in (("纳指", "cache_bt/ixic.json"), ("上证", "cache_bt/sh000001.json")):
        try:
            b = json.load(open(path))
            bs = pd.Series(b["close"], index=pd.to_datetime(b["dates"])).sort_index()
            bs = bs.dropna().reindex(sv.index, method="ffill").dropna()
            if len(bs):
                benches[name] = bs
        except Exception:
            pass

    from etf_rot_signal import plot_results
    html = plot_results(sv, benches, trades, names, ts,
                        title_text=(f"趋势跟随 · 拉长市场 2016+ (指数代理池)<br><sup>"
                                    f"MA{200}+动量{180}日 动量差&gt;1.0才切换 | 底仓{base_w:.0%} {names[base_etf]} 止损20% | "
                                    f"{BT_START} ~ {sv.index[-1].date()} | 池=13个sina行业指数代理+黄金/纳指真ETF</sup>"))
    html_file = f"etf_rot_long_result_{ts}.html"
    with open(html_file, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"HTML -> {html_file}")


if __name__ == "__main__":
    main()
