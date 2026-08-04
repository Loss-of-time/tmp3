#!/usr/bin/env python3
"""探索候选行业/主题 ETF: 拉取日线到 cache_bt/etf_explore/, 并打印腾讯实时名称核对代码。"""

import json
import os
import time
import urllib.request

from fetch_etf_industry import CACHE, TENCENT_URL, _tencent_page

EXPLORE_DIR = "cache_bt/etf_explore"

# 候选池 (代码: 预置名, 用于核对)
EXPLORE = {
    # A股行业/主题 (未入池)
    "512800": "银行",
    "512200": "房地产",
    "512170": "医疗",
    "515170": "食品饮料",
    "516110": "汽车",
    "159996": "家电",
    "515210": "钢铁",
    "159745": "建材",
    "159825": "农业",
    "159865": "养殖",
    "159766": "旅游",
    "516010": "游戏",
    "515070": "人工智能",
    "512720": "计算机",
    "159852": "软件",
    "516510": "云计算",
    "515880": "通信",
    "515260": "电子",
    "159732": "消费电子",
    "516780": "稀土",
    "516950": "基建",
    "159667": "工业母机",
    "588000": "科创50",
    "159755": "电池",
    "512070": "证券保险",
    "159992": "创新药",
    "516160": "新能源",
    "512290": "生物医药",
    "159619": "基建",
    # 港股/跨境
    "513100": "纳指ETF",
    "513500": "标普500",
    "513180": "恒生科技",
    "513330": "恒生互联网",
    "513060": "恒生医疗",
    "513050": "中概互联",
    "513520": "日经ETF",
    "159920": "恒生ETF",
    # 商品/其他
    "159985": "豆粕",
    "501018": "原油",
    "159980": "有色金属",
}


def verify_names():
    """用腾讯实时行情接口核对代码->名称。"""
    out = {}
    codes = list(EXPLORE)
    for i in range(0, len(codes), 40):
        q = ",".join(("sz" if c.startswith("1") else "sh") + c for c in codes[i:i + 40])
        url = f"http://qt.gtimg.cn/q={q}"
        try:
            raw = urllib.request.urlopen(urllib.request.Request(
                url, headers={"User-Agent": "Mozilla/5.0"}), timeout=10).read().decode("gbk")
            for line in raw.split(";"):
                if "=" not in line or 'v_' not in line:
                    continue
                body = line.split("=", 1)[1].strip('"').split("~")
                if len(body) > 2:
                    out[body[2]] = body[1]
        except Exception as e:
            print("verify 失败:", str(e)[:80])
        time.sleep(0.3)
    return out


def fetch(code, name):
    f = os.path.join(EXPLORE_DIR, f"{code}.json")
    if os.path.exists(f):
        return "cached"
    for attempt in range(8):
        try:
            rows, end = [], "2026-07-31"
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
            out = {"code": code, "name": name, "dates": [r[0] for r in dedup],
                   "open": [r[1] for r in dedup], "close": [r[2] for r in dedup]}
            with open(f, "w") as fh:
                json.dump(out, fh, ensure_ascii=False)
            return f"{len(out['dates'])} 行"
        except Exception as e:
            if attempt == 7:
                return f"fail: {str(e)[:60]}"
            time.sleep(15)
    return "unreachable"


def main():
    os.makedirs(EXPLORE_DIR, exist_ok=True)
    print("== 名称核对 (腾讯实时) ==")
    real = verify_names()
    for code, name in EXPLORE.items():
        mark = "OK" if real.get(code) else "??"
        print(f"  {code} {mark}: {name} -> {real.get(code, '未找到')}")
    print("== 拉取历史 ==")
    for code, name in EXPLORE.items():
        msg = fetch(code, name)
        print(f"  {code} {name}: {msg}")


if __name__ == "__main__":
    main()
