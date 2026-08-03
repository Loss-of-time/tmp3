#!/usr/bin/env python3
"""拉取行业板块 ETF 前复权日线缓存到 cache_bt/etf_industry/。

数据源: akshare 东财 (fund_etf_hist_em, adjust=qfq)。频繁限流, 失败隔 15s 重试。
"""

import json
import os
import time

import akshare as ak

CACHE = "cache_bt/etf_industry"

ETFS = {
    "512000": "券商",
    "512660": "军工",
    "512690": "白酒",
    "512010": "医药",
    "512480": "半导体",
    "515790": "光伏",
    "515030": "新能源车",
    "512400": "有色金属",
    "515220": "煤炭",
    "512980": "传媒",
    "515050": "5G通信",
    "159995": "芯片",
}

LOWVOL = {
    "512890": "红利低波",
    "510880": "红利ETF",
}

START = "20170101"
END = "20260731"


def fetch(code, name):
    f = os.path.join(CACHE, f"{code}.json")
    if os.path.exists(f):
        return True, "cached"
    for attempt in range(12):
        try:
            df = ak.fund_etf_hist_em(symbol=code, period="daily",
                                     start_date=START, end_date=END, adjust="qfq")
            out = {
                "code": code, "name": name,
                "dates": df["日期"].astype(str).tolist(),
                "close": [round(float(x), 4) for x in df["收盘"]],
            }
            with open(f, "w") as fh:
                json.dump(out, fh, ensure_ascii=False)
            return True, f"{len(out['dates'])} 行"
        except Exception as e:
            if attempt == 11:
                return False, f"fail: {str(e)[:60]}"
            time.sleep(15)
    return False, "unreachable"


def main():
    os.makedirs(CACHE, exist_ok=True)
    for code, name in {**ETFS, **LOWVOL}.items():
        ok, msg = fetch(code, name)
        print(f"{code} {name}: {msg}")


if __name__ == "__main__":
    main()
