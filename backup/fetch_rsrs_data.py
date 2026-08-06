#!/usr/bin/env python3
"""拉取带 high/low 的日线 (腾讯 qfq), 供 RSRS 复刻使用。"""
import json
import os
import urllib.request

import fetch_etf_industry as f

OUT = "cache_bt/rsrs_test"


def fetch(code, name):
    fh = os.path.join(OUT, f"{code}.json")
    if os.path.exists(fh):
        return "cached"
    pref = ("sz" if code.startswith("1") else "sh") + code
    rows, end = [], f.END_TX
    for attempt in range(12):
        try:
            while True:
                url = f"{f.TENCENT_URL}?param={pref},day,,{end},640,qfq"
                req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
                d = json.load(urllib.request.urlopen(req, timeout=15))
                day = d["data"][pref].get("day") or d["data"][pref].get("qfqday")
                page = [(r[0], r[1], r[2], r[3], r[4]) for r in day]
                page = [(x[0], float(x[1]), float(x[2]), float(x[3]), float(x[4]))
                        for x in page]
                rows = page + rows
                if len(page) < 640:
                    break
                end = page[0][0]
            seen, dedup = set(), []
            for r in rows:
                if r[0] not in seen:
                    seen.add(r[0])
                    dedup.append(r)
            out = {"code": code, "name": name,
                   "dates": [r[0] for r in dedup],
                   "open": [r[1] for r in dedup], "close": [r[2] for r in dedup],
                   "high": [r[3] for r in dedup], "low": [r[4] for r in dedup]}
            with open(fh, "w") as fh2:
                json.dump(out, fh2)
            return f"{len(out['dates'])} 行"
        except Exception as e:
            if attempt == 11:
                return f"fail: {e}"
            import time
            time.sleep(15)


if __name__ == "__main__":
    os.makedirs(OUT, exist_ok=True)
    for code, name in [("159941", "纳指100"), ("518880", "黄金"),
                       ("159915", "创业板100"), ("159985", "豆粕")]:
        print(code, name, fetch(code, name))
