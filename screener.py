#!/usr/bin/env python3
"""A-share stock screener: PE/PB 5yr percentile < 50%, in 沪深300 or 中证500."""

import json
import os
import sys
import time
from datetime import datetime, timedelta

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

# ponytail: baostock uses removed pandas3 DataFrame.append
pd.DataFrame.append = lambda self, other, ignore_index=False, sort=False: pd.concat(
    [self, other], ignore_index=ignore_index, sort=sort
)

import akshare as ak
import baostock as bs

CACHE_DIR = "cache"
INDICES = {"000300": "沪深300", "000905": "中证500"}
EXCLUDE_CSRC = {
    "J66货币金融服务",          # 银行
    "B06煤炭开采和洗选业",       # 煤炭
    "B07石油和天然气开采业",     # 石油石化
    "C25石油、煤炭及其他燃料加工业", # 石油+煤炭
    "B09有色金属矿采选业",       # 有色
    "C32有色金属冶炼和压延加工业", # 有色
    "K70房地产业",              # 房地产
}
YEARS = 5
MAX_PERCENTILE = 50

os.makedirs(CACHE_DIR, exist_ok=True)


def akshare_to_baostock(code):
    """Convert akshare 6-digit code to baostock format."""
    if code.startswith(("0", "3")):
        return f"sz.{code}"
    if code.startswith("6"):
        return f"sh.{code}"
    if code.startswith(("4", "8")):
        return f"bj.{code}"
    return f"sh.{code}"


def cache_path(code):
    return os.path.join(CACHE_DIR, f"{code}.json")


def load_cache(code):
    p = cache_path(code)
    if os.path.exists(p):
        with open(p) as f:
            return json.load(f)
    return None


def save_cache(code, data):
    with open(cache_path(code), "w") as f:
        json.dump(data, f, ensure_ascii=False)


def fetch_one(code):
    cached = load_cache(code)
    if cached:
        return cached

    bs_code = akshare_to_baostock(code)
    try:
        rs = bs.query_history_k_data_plus(
            bs_code, "date,peTTM,pbMRQ",
            start_date=(datetime.now() - timedelta(days=YEARS * 366)).strftime("%Y-%m-%d"),
            end_date=datetime.now().strftime("%Y-%m-%d"),
            frequency="d", adjustflag="3",
        )
        raw = rs.get_data()
    except Exception:
        return None

    if raw.empty:
        return None

    raw["peTTM"] = pd.to_numeric(raw["peTTM"], errors="coerce")
    raw["pbMRQ"] = pd.to_numeric(raw["pbMRQ"], errors="coerce")

    pe_vals = raw["peTTM"].dropna()
    pb_vals = raw["pbMRQ"].dropna()

    if len(pe_vals) < 100 or len(pb_vals) < 100:
        return None

    latest_pe = float(pe_vals.iloc[-1])
    latest_pb = float(pb_vals.iloc[-1])

    if latest_pe <= 0 or latest_pb <= 0:
        return None

    pe_pct = (pe_vals <= latest_pe).mean() * 100
    pb_pct = (pb_vals <= latest_pb).mean() * 100

    result = {
        "code": code,
        "pe": round(latest_pe, 2),
        "pb": round(latest_pb, 2),
        "pe_percentile": round(float(pe_pct), 1),
        "pb_percentile": round(float(pb_pct), 1),
        "data_points": len(raw),
    }
    save_cache(code, result)
    return result


def fetch_all_pe_pb(codes):
    results = {}
    total = len(codes)
    t0 = time.time()
    for i, code in enumerate(codes, 1):
        r = fetch_one(code)
        if r:
            results[code] = r
        if i % 50 == 0 or i == total:
            elapsed = time.time() - t0
            eta = elapsed / i * (total - i)
            print(f"      {i}/{total} ({i/total*100:.0f}%) 耗时:{elapsed:.0f}s 预计剩余:{eta:.0f}s")
    return results


def get_index_stocks():
    stocks = {}
    for idx_code, idx_name in INDICES.items():
        try:
            df = ak.index_stock_cons_csindex(symbol=idx_code)
            df["code"] = df["成分券代码"].astype(str).str.zfill(6)
            for code in df["code"]:
                stocks[code] = idx_name
        except Exception as e:
            print(f"  warn: {idx_name} fetch failed: {e}", file=sys.stderr)
    return stocks


def get_industry_map():
    try:
        rs = bs.query_stock_industry()
        df = rs.get_data()
        df["ak_code"] = df["code"].str.replace("sh.", "").str.replace("sz.", "").str.replace("bj.", "")
        return dict(zip(df["ak_code"], df["industry"]))
    except Exception as e:
        print(f"  warn: industry fetch failed: {e}", file=sys.stderr)
        return {}


def get_stock_names():
    try:
        rs = bs.query_stock_basic()
        df = rs.get_data()
        df = df[df["type"] == "1"]  # only stocks, not indices
        df["ak_code"] = df["code"].str.extract(r"\.(\d{6})$")[0]
        return dict(zip(df["ak_code"], df["code_name"]))
    except Exception:
        return {}


def make_html(df, timestamp):
    fig = make_subplots(
        rows=1, cols=2,
        subplot_titles=("PE分位 vs PB分位", "分位数分布"),
        column_widths=[0.55, 0.45],
    )

    hover_text = [
        f"{n}<br>{c}<br>PE分位:{pe}% PB分位:{pb}%<br>PE:{pe_v} PB:{pb_v}"
        for n, c, pe, pb, pe_v, pb_v in zip(
            df["name"], df["code"],
            df["pe_percentile"], df["pb_percentile"],
            df["pe"], df["pb"],
        )
    ]

    fig.add_trace(
        go.Scatter(
            x=df["pe_percentile"], y=df["pb_percentile"],
            mode="markers", text=hover_text, hoverinfo="text",
            marker=dict(size=8, opacity=0.7),
        ),
        row=1, col=1,
    )
    fig.add_hline(y=MAX_PERCENTILE, line_dash="dash", line_color="gray", row=1, col=1)
    fig.add_vline(x=MAX_PERCENTILE, line_dash="dash", line_color="gray", row=1, col=1)

    fig.add_trace(
        go.Histogram(x=df["pe_percentile"], nbinsx=20, name="PE分位",
                     marker_color="steelblue", opacity=0.7),
        row=1, col=2,
    )
    fig.add_trace(
        go.Histogram(x=df["pb_percentile"], nbinsx=20, name="PB分位",
                     marker_color="coral", opacity=0.7),
        row=1, col=2,
    )

    fig.update_xaxes(title_text="PE 5年分位数 (%)", range=[0, 100], row=1, col=1)
    fig.update_yaxes(title_text="PB 5年分位数 (%)", range=[0, 100], row=1, col=1)
    fig.update_xaxes(title_text="分位数 (%)", row=1, col=2)
    fig.update_yaxes(title_text="数量", row=1, col=2)

    fig.update_layout(
        title_text=(
            f"选股结果 ({timestamp})<br><sup>"
            f"条件: PE分位&lt;{MAX_PERCENTILE}% AND PB分位&lt;{MAX_PERCENTILE}% | "
            f"沪深300∪中证500 | "
            f"排除:银行/煤炭/石油石化/有色/房地产/ST | "
            f"共{len(df)}只</sup>"
        ),
        showlegend=False, height=600,
    )
    return fig.to_html(include_plotlyjs="cdn", full_html=True)


def main():
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    lg = bs.login()
    if lg.error_code != "0":
        print(f"baostock login failed: {lg.error_msg}", file=sys.stderr)
        sys.exit(1)

    print("[1/6] 获取沪深300+中证500成分股...")
    index_stocks = get_index_stocks()
    codes = list(index_stocks.keys())
    print(f"      共 {len(codes)} 只")

    print(f"[2/6] 获取股票名称...")
    all_names = get_stock_names()

    print(f"[3/6] 获取PE/PB历史分位 (缓存:{CACHE_DIR}/, 串行)...")
    pe_pb_data = fetch_all_pe_pb(codes)
    print(f"      有效数据: {len(pe_pb_data)} 只")

    df = pd.DataFrame(pe_pb_data.values())
    if df.empty:
        print("无有效PE/PB数据.", file=sys.stderr)
        bs.logout()
        sys.exit(1)

    print("[4/6] 应用筛选条件...")
    df = df[(df["pe_percentile"] < MAX_PERCENTILE) & (df["pb_percentile"] < MAX_PERCENTILE)]
    print(f"      PE分位<{MAX_PERCENTILE}% AND PB分位<{MAX_PERCENTILE}%: {len(df)} 只")

    ind_map = get_industry_map()
    if ind_map:
        df["industry"] = df["code"].map(ind_map)
        df = df[~df["industry"].isin(EXCLUDE_CSRC)]
        print(f"      排除行业后: {len(df)} 只")

    df["name"] = df["code"].map(all_names)
    df = df[~df["name"].str.contains("ST", na=False)]
    print(f"      排除ST后: {len(df)} 只")

    df = df.sort_values("pe_percentile")

    print("[5/6] 导出CSV...")
    csv_file = f"result_{timestamp}.csv"
    out_cols = ["code", "name", "pe", "pb", "pe_percentile", "pb_percentile", "data_points"]
    df[out_cols].to_csv(csv_file, index=False, encoding="utf-8-sig")
    print(f"      -> {csv_file}")

    print("[6/6] 生成可视化HTML...")
    html_file = f"result_{timestamp}.html"
    with open(html_file, "w", encoding="utf-8") as f:
        f.write(make_html(df, timestamp))
    print(f"      -> {html_file}")

    bs.logout()

    print(f"\n完成. {len(df)} 只股票入选.")
    if not df.empty:
        print(df[["code", "name", "pe_percentile", "pb_percentile"]].to_string(index=False))


if __name__ == "__main__":
    main()
