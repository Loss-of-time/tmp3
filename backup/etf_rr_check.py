#!/usr/bin/env python3
"""池内 ETF 收益/波动/夏普: 共同窗口比较, 找"跑不赢国债"的标的。"""
import glob, json, math, os

CACHE = "cache_bt/etf_industry"
RF = 0.025


def stats(d, start):
    c = [x for x in d if x[0] >= start]
    if len(c) < 252:
        return None
    r = [c[i][1] / c[i - 1][1] - 1 for i in range(1, len(c))]
    n = len(r)
    ann_r = (c[-1][1] / c[0][1]) ** (252 / n) - 1
    ann_v = math.sqrt(sum(x * x for x in r) / n) * math.sqrt(252)
    return ann_r, ann_v, (ann_r - RF) / ann_v


def report(start, label):
    print(f"== {label} 起 ({start}) ==")
    rows = []
    for f in sorted(glob.glob(os.path.join(CACHE, "*.json"))):
        d = json.load(open(f))
        s = stats(list(zip(d["dates"], d["close"])), start)
        if s:
            rows.append((d["name"],) + s)
    rows.sort(key=lambda x: -x[3])
    print(f"{'名称':<8}{'年化收益':>9}{'年化波动':>9}{'夏普':>7}")
    for name, ar, av, sh in rows:
        bad = "  <-- 跑不赢国债" if ar < RF else ""
        print(f"{name:<8}{ar:>8.1%}{av:>9.1%}{sh:>7.2f}{bad}")
    print()


report("2022-01-01", "全池共同窗")
report("2019-01-01", "早期标的共同窗")
report("2017-01-01", "最老标的共同窗")
