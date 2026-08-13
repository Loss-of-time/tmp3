#!/bin/sh
# 每 4 小时运行一次模拟盘更新; 同时常驻 http.server 服务 docs。
set -e

(
  while true; do
    python3 paper_trade_signal.py || echo "运行失败, 下轮重试" >&2
    sleep 14400
  done
) &

exec python3 -m http.server 8077 --directory docs --bind 0.0.0.0
