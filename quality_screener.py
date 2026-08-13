#!/usr/bin/env python3
"""v8 盈利质量选股器：PE/PB 分位 + 4 项现金含量/话语权指标。

指标 (方向: 好):
  现金支出利润差   经营现金支出单季同比 - 归母净利润单季同比   (低)
  利润收入增速差   营业利润单季同比 - 营业收入单季同比         (高)
  经营现金净余率   (经营现金流净额-应交税费-其他应付款-其他流动负债)/销售收现  (高)
  应收账款其他应付比 应收账款/其他应付款                      (低)
"""

import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta

import pandas as pd
from tqdm import tqdm

# ponytail: baostock uses removed pandas3 DataFrame.append
pd.DataFrame.append = lambda self, other, ignore_index=False, sort=False: pd.concat(
    [self, other], ignore_index=ignore_index, sort=sort
)

import akshare as ak
import baostock as bs

CACHE_DIR = "cache_fin"
OUTPUT_DIR = "output"   # 统一输出目录
INDICES = {"000300": "沪深300", "000905": "中证500"}
EXCLUDE_CSRC = {
    "J66货币金融服务", "B06煤炭开采和洗选业", "B07石油和天然气开采业",
    "C25石油、煤炭及其他燃料加工业", "B09有色金属矿采选业", "C32有色金属冶炼和压延加工业",
    "K70房地产业",
}
YEARS = 5
MAX_PERCENTILE = 50

os.makedirs(CACHE_DIR, exist_ok=True)

CF_FIELDS = {
    "sales_cash": "销售商品、提供劳务收到的现金",
    "cash_out_op": "经营活动现金流出小计",
    "cfo": "经营活动产生的现金流量净额",
}
PL_FIELDS = {
    "op_profit": "营业利润",
    "revenue": "营业收入",
    "np_parent": "归属于母公司所有者的净利润",
}
BS_FIELDS = {
    "tax_payable": "应交税费",
    "other_payable": "其他应付款合计",
    "other_cur_liab": "其他流动负债",
    "ar": "应收账款",
}
SHEETS = {"现金流量表": CF_FIELDS, "利润表": PL_FIELDS, "资产负债表": BS_FIELDS}


def akshare_to_baostock(code):
    if code.startswith(("0", "3")):
        return f"sz.{code}"
    if code.startswith("6"):
        return f"sh.{code}"
    return f"sh.{code}"


def cache_path(code):
    return os.path.join(CACHE_DIR, f"{code}.json")


def load_cache(code):
    p = cache_path(code)
    if os.path.exists(p):
        with open(p) as f:
            return __import__("json").load(f)
    return None


def save_cache(code, data):
    with open(cache_path(code), "w") as f:
        __import__("json").dump(data, f, ensure_ascii=False)


def fetch_financials(code):
    cached = load_cache(code)
    if cached:
        return cached

    ak_code = f"sh{code}" if code.startswith("6") else f"sz{code}"
    fin = {}
    try:
        for sheet, fields in SHEETS.items():
            df = ak.stock_financial_report_sina(stock=ak_code, symbol=sheet)
            df = df.sort_values("报告日").reset_index(drop=True)
            fin[sheet] = {
                str(row["报告日"]): {k: row.get(v) for k, v in fields.items()}
                for _, row in df.iterrows()
            }
    except Exception as e:
        print(f"  warn: {code} fetch failed: {e}", file=sys.stderr)
        return None

    result = {"code": code, "fin": fin}
    save_cache(code, result)
    return result


def parse_num(v):
    try:
        f = float(v)
        return f if pd.notna(f) else None
    except (TypeError, ValueError):
        return None


def single_quarter(fin, sheet, field, report_date):
    """累计报表 -> 单季值。report_date 形如 '20260331'。"""
    dates = sorted(fin[sheet].keys(), reverse=True)
    if report_date not in dates:
        return None
    cum_now = parse_num(fin[sheet][report_date].get(field))
    if cum_now is None:
        return None
    prev = None
    for d in dates:
        if d < report_date and d[:4] == report_date[:4]:
            prev = d
            break
    if prev:
        cum_prev = parse_num(fin[sheet][prev].get(field))
        if cum_prev is None:
            return None
        return cum_now - cum_prev
    return cum_now


def yoy(fin, sheet, field, report_date):
    q_now = single_quarter(fin, sheet, field, report_date)
    if q_now is None:
        return None
    year, month = report_date[:4], report_date[4:]
    last_year = f"{int(year) - 1}{month}"
    q_ly = single_quarter(fin, sheet, field, last_year)
    if q_ly is None or q_ly == 0:
        return None
    return q_now / q_ly - 1


def latest_report_date(fin):
    dates = sorted(fin["现金流量表"].keys(), reverse=True)
    return dates[0]


def compute_quality(fin):
    """返回 4 指标字典。最新报告期。"""
    rd = latest_report_date(fin)
    out = {"report_date": rd}
    out["cash_out_op_yoy"] = yoy(fin, "现金流量表", "cash_out_op", rd)
    out["np_parent_yoy"] = yoy(fin, "利润表", "np_parent", rd)
    out["op_profit_yoy"] = yoy(fin, "利润表", "op_profit", rd)
    out["revenue_yoy"] = yoy(fin, "利润表", "revenue", rd)

    out["cash_gap"] = None
    if out["cash_out_op_yoy"] is not None and out["np_parent_yoy"] is not None:
        out["cash_gap"] = out["cash_out_op_yoy"] - out["np_parent_yoy"]
    out["profit_gap"] = None
    if out["op_profit_yoy"] is not None and out["revenue_yoy"] is not None:
        out["profit_gap"] = out["op_profit_yoy"] - out["revenue_yoy"]

    cfo = parse_num(fin["现金流量表"][rd].get("cfo"))
    sales_cash = parse_num(fin["现金流量表"][rd].get("sales_cash"))
    tax = parse_num(fin["资产负债表"][rd].get("tax_payable")) or 0
    op = parse_num(fin["资产负债表"][rd].get("other_payable")) or 0
    ocl = parse_num(fin["资产负债表"][rd].get("other_cur_liab")) or 0
    ar = parse_num(fin["资产负债表"][rd].get("ar"))
    out["cfo_net_rate"] = None
    if cfo is not None and sales_cash and sales_cash > 0:
        out["cfo_net_rate"] = (cfo - tax - op - ocl) / sales_cash
    out["ar_op_ratio"] = None
    if ar is not None and op and op > 0:
        out["ar_op_ratio"] = ar / op
    return out


def fetch_pe_pb(code):
    """复用 v0 逻辑：5年PE/PB分位。结果缓存 cache_quality/。"""
    cp = f"cache_quality/{code}.json"
    if os.path.exists(cp):
        with open(cp) as f:
            return __import__("json").load(f)
    try:
        bs_code = akshare_to_baostock(code)
        rs = bs.query_history_k_data_plus(
            bs_code, "date,peTTM,pbMRQ",
            start_date=(datetime.now() - timedelta(days=YEARS * 366)).strftime("%Y-%m-%d"),
            end_date=datetime.now().strftime("%Y-%m-%d"),
            frequency="d", adjustflag="3",
        )
        raw = rs.get_data()
        if raw.empty:
            return None
        raw["peTTM"] = pd.to_numeric(raw["peTTM"], errors="coerce")
        raw["pbMRQ"] = pd.to_numeric(raw["pbMRQ"], errors="coerce")
        pe_vals, pb_vals = raw["peTTM"].dropna(), raw["pbMRQ"].dropna()
        if len(pe_vals) < 100 or len(pb_vals) < 100:
            return None
        lp, lb = float(pe_vals.iloc[-1]), float(pb_vals.iloc[-1])
        if lp <= 0 or lb <= 0:
            return None
        result = {
            "pe": round(lp, 2), "pb": round(lb, 2),
            "pe_percentile": round(float((pe_vals <= lp).mean() * 100), 1),
            "pb_percentile": round(float((pb_vals <= lb).mean() * 100), 1),
        }
        save_pe_pb(code, result)
        return result
    except Exception:
        return None


def save_pe_pb(code, data):
    os.makedirs("cache_quality", exist_ok=True)
    with open(f"cache_quality/{code}.json", "w") as f:
        __import__("json").dump(data, f, ensure_ascii=False)


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


def get_stock_names():
    try:
        rs = bs.query_stock_basic()
        df = rs.get_data()
        df = df[df["type"] == "1"]
        df["ak_code"] = df["code"].str.extract(r"\.(\d{6})$")[0]
        return dict(zip(df["ak_code"], df["code_name"]))
    except Exception:
        return {}


def get_industry_map():
    try:
        rs = bs.query_stock_industry()
        df = rs.get_data()
        df["ak_code"] = df["code"].str.replace("sh.", "").str.replace("sz.", "").str.replace("bj.", "")
        return dict(zip(df["ak_code"], df["industry"]))
    except Exception:
        return {}


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="只测前N只(调试)")
    args = ap.parse_args()

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    lg = bs.login()
    if lg.error_code != "0":
        sys.exit(1)

    index_stocks = get_index_stocks()
    codes = list(index_stocks.keys())
    if args.limit:
        codes = codes[:args.limit]
    print(f"[1/5] 成分股 {len(codes)} 只")

    all_names = get_stock_names()

    print("[2/5] 拉取财报 (并发8, 缓存 cache_fin/)...")
    t0 = time.time()
    fin_data = {}
    with ThreadPoolExecutor(max_workers=8) as ex:
        futs = {ex.submit(fetch_financials, c): c for c in codes}
        for fut in tqdm(as_completed(futs), total=len(codes), desc="财报", unit="只"):
            c = futs[fut]
            r = fut.result()
            if r:
                fin_data[c] = r
    print(f"      有效财报: {len(fin_data)} 只 (耗时{time.time()-t0:.0f}s)")

    print("[3/5] 计算质量指标...")
    rows = []
    for code, data in fin_data.items():
        q = compute_quality(data["fin"])
        q["code"] = code
        rows.append(q)
    qdf = pd.DataFrame(rows)

    print("[4/5] 计算PE/PB分位...")
    t0 = time.time()
    pe_pb = {}
    for code in tqdm(qdf["code"], desc="PE/PB", unit="只"):
        r = fetch_pe_pb(code)
        if r:
            pe_pb[code] = r
    print(f"      PE/PB 有效: {len(pe_pb)} 只 (耗时{time.time()-t0:.0f}s)")
    qdf = qdf.join(pd.DataFrame.from_dict(pe_pb, orient="index"), on="code")
    qdf = qdf.dropna(subset=["pe_percentile", "pb_percentile"])
    print(f"      PE/PB+财报 有效: {len(qdf)} 只")

    print("[5/5] 筛选+排序...")
    qdf = qdf[(qdf["pe_percentile"] < MAX_PERCENTILE) & (qdf["pb_percentile"] < MAX_PERCENTILE)]
    ind_map = get_industry_map()
    qdf["industry"] = qdf["code"].map(ind_map)
    qdf = qdf[~qdf["industry"].isin(EXCLUDE_CSRC)]
    qdf["name"] = qdf["code"].map(all_names)
    qdf = qdf[~qdf["name"].str.contains("ST", na=False)]

    # 4 指标方向打分: 方向好的为 +1(越低越好则取负)。百分位排名合成。
    score_cols = {"cash_gap": -1, "profit_gap": 1, "cfo_net_rate": 1, "ar_op_ratio": -1}
    qdf["quality_score"] = 0.0
    for col, sign in score_cols.items():
        valid = qdf[col].notna()
        if valid.sum() == 0:
            continue
        qdf.loc[valid, "quality_score"] += sign * qdf.loc[valid, col].rank(pct=True)
    qdf["quality_score"] = qdf["quality_score"] / len(score_cols)

    qdf = qdf.sort_values("quality_score", ascending=False)
    cols = ["code", "name", "industry", "quality_score",
            "pe_percentile", "pb_percentile", "pe", "pb",
            "cash_gap", "profit_gap", "cfo_net_rate", "ar_op_ratio", "report_date"]
    out = qdf[cols]
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    csv_file = os.path.join(OUTPUT_DIR, f"quality_result_{timestamp}.csv")
    out.to_csv(csv_file, index=False, encoding="utf-8-sig")
    print(f"      -> {csv_file}")
    bs.logout()
    print(f"\n完成. 入选 {len(out)} 只 (按质量分排序)")
    print(out.head(30).to_string(index=False))


if __name__ == "__main__":
    main()
