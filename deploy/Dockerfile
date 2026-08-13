FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 TZ=Asia/Shanghai

RUN apt-get update && apt-get install -y --no-install-recommends tzdata && rm -rf /var/lib/apt/lists/* \
 && pip install --no-cache-dir akshare baostock pandas numpy plotly

WORKDIR /app
COPY paper_trade_signal.py paper_trade.py rot_core.py backtest.py dynpool.py fetch_etf_industry.py etf_rotation.py etf_rot_signal.py ./
COPY paper_state.json ./
COPY cache_bt/etf_industry cache_bt/etf_industry
COPY cache_bt/etf_explore cache_bt/etf_explore
COPY cache_bt/hs300.json cache_bt/ixic.json cache_bt/sh000001.json cache_bt/
COPY paper_signal_state.json paper_signal_state.json
COPY deploy/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 8077
CMD ["/entrypoint.sh"]
