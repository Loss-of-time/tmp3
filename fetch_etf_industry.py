#!/usr/bin/env python3
"""拉取行业板块 ETF 前复权日线缓存到 cache_bt/etf_industry/。

数据源: 腾讯 fqkline (qfq 前复权, 单次最多 640 行, 分页)。东财历史接口(akshare
fund_etf_hist_em) 2026-08 曾完全不可用, 故改用腾讯; 已验证腾讯 qfq 与东财 qfq
一致 (512000 max diff 0.001, 仅浮点舍入)。
"""

import json
import os
import time
import urllib.request

TENCENT_URL = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"

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

NEW_ETFS = {
    "562500": "机器人",
    "518880": "黄金",
    "516020": "化工",
    "159928": "消费",
    "159529": "标普消费",
    "159611": "电力",
    "513850": "美国50",
    "159502": "标普生物科技",
}

LOWVOL = {
    "512890": "红利低波",
    "510880": "红利ETF",
}

START = "20170101"
END = "20260731"
END_TX = "2026-07-31"


def _tencent_page(code, n, end):
    """拉一页腾讯 qfq 日线, 返回 [(date, open, close), ...] 升序。"""
    pref = ("sz" if code.startswith("1") else "sh") + code
    url = (f"{TENCENT_URL}?param={pref},day,,{end},{n},qfq")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    d = json.load(urllib.request.urlopen(req, timeout=15))
    day = d["data"][pref].get("day") or d["data"][pref].get("qfqday")
    return [(r[0], round(float(r[1]), 4), round(float(r[2]), 4)) for r in day]


def fetch(code, name):
    f = os.path.join(CACHE, f"{code}.json")
    if os.path.exists(f):
        return True, "cached"
    for attempt in range(12):
        try:
            rows, end = [], END_TX
            while True:
                page = _tencent_page(code, 640, end)
                rows = page + rows
                if len(page) < 640:
                    break
                end = page[0][0]
            seen = set()
            dedup = []
            for r in rows:
                if r[0] not in seen:
                    seen.add(r[0])
                    dedup.append(r)
            rows = dedup
            out = {
                "code": code, "name": name,
                "dates": [r[0] for r in rows],
                "open": [r[1] for r in rows],
                "close": [r[2] for r in rows],
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
    for code, name in {**ETFS, **NEW_ETFS, **LOWVOL}.items():
        ok, msg = fetch(code, name)
        print(f"{code} {name}: {msg}")


if __name__ == "__main__":
    main()
