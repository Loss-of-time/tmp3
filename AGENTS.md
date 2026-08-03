# 自用选股器

## 目的

根据用户给定筛选条件选出合适的股票代码，并提供排序和可视化功能

## 进度

- [x] v0 完成：`screener.py` 单脚本跑通
- [x] v1 回测：`backtest.py` PE 10年分位策略回测
- [x] v2 威科夫吸筹策略：`wyckoff.py` 事件驱动回测
- [x] v3 单票动量策略：`momentum.py` 时间序列动量（资金量小，单票满仓）
- [x] v4 ETF 择时：`etf_timing.py` 牛熊开关（回撤小，可不满仓）
- [x] v5 行业ETF轮动：`etf_rotation.py` 动量轮动+牛熊开关（收益高）

## v5 行业ETF动量轮动 (`etf_rotation.py`)

- 只在调仓日(每 REBAL=40 交易日)检视：从"收盘>MA200牛熊线"的行业ETF里挑过去 LOOKBACK=180 日动量最强的一只持仓；都不站上则空仓吃现金 2%
- 持仓每日检查：跌破 MA200(趋势破坏)或从持仓峰值回撤 20%(移动止损) 即离场，等下一个调仓日再进；不在板块间频繁切换
- **低波底仓 BASE_W=45%**：此比例资金始终持有红利低波ETF(512890)，剩余 55% 做板块轮动。稀释单板块暴涨暴跌（2026-06 半导体 +47% 后 7 月 -33% 全回吐是纯轮动主要回撤来源）
- 数据缓存 `cache_bt/etf_industry/{code}.json`（东财 qfq 前复权，12 只行业ETF：券商/军工/白酒/医药/半导体/光伏/新能源车/有色/煤炭/传媒/5G/芯片 + 红利低波/红利ETF），拉取脚本 `fetch_etf_industry.py`（akshare 限流隔 15s 重试）
- 回测区间 2020-01 ~ 2026-07，基准沪深300，成本万三双向
- **结果（底仓45%+止损20%）：年化 20.2% vs 基准 1.5%，总收益 220.0%，夏普 0.74，回撤 27.1%，买入 10 次+切换 5 次，2026 回撤仅 20.3%**（v5 早期纯轮动半仓：14.8%/31.2%，2026 回撤 30%+）
- 扫参：长动量(120-180日)是关键；短动量(40-60日)高频切换必亏（LB60 默认参数下 -11.2%/回撤 75%），重蹈 v3 追热覆辙；REBAL 越大越稳；MA200 略优于 150/250；底仓 35-55%+止损 20% 一档 sharpe 0.72-0.74，再高底仓收益下降
- **稳定性测试 `etf_rot_sweep.py`（逐参数扰动，默认值★）**：BASE_W 最稳（0.35-0.60 年化 21.5→18.1%、夏普恒 0.74，回撤随底仓单调下降）；TRAIL 0.2-0.25 平台（夏普 0.72-0.74）；MA_N 180-220 平台（16-20%）；敏感参数是 LOOKBACK（峰值 150-180 年化 20%+，240 跌到 8%）与 REBAL（峰值 40=20.2%，80 跌到 8.5%）。默认参数位于各参数峰值附近，稳健性可接受

## v4 ETF 牛熊开关择时 (`etf_timing.py`)

- 逻辑极简：每日收盘 > MA200 则持有沪深300ETF(510300)（默认权重 POS_WEIGHT=0.6≈半仓），否则空仓吃现金 2% 年化
- 低频（2020-2026 共 18 次买入信号，约每 4 个月一次），用仓位和空仓控制回撤，适合资金量小/怕回撤的用户
- 数据源坑：baostock 的 510300 只回 2026 起 139 行（历史缺失）不可用；指数 PE 择时不可行（baostock PE 2012-2025 全空）；akshare 东财源 qfq 可拿全历史但频繁限流
- 数据缓存 `cache_bt/etf510300_qfq.json`（{'dates':[], 'close':[]}，前复权，2012-05-28 ~ 2026-07-31，3446 行），直接读无需网络
- 回测区间 2020-01 ~ 2026-07，基准沪深300，成本万三双向
- **结果（半仓 0.6）：年化 3.1% vs 基准 1.5%，总收益 21.5%，夏普 0.11，回撤仅 13.2%，18 次买入**
- 参数测试（2015 起全区间）：双均线金叉死叉全差；牛熊开关 MA200 满仓 dd 31.8%、半仓 dd 17.2% 最佳；MA100 换手太高反而差；周线采样不优
- 可调 MA_N / POS_WEIGHT 后重跑

## v3 单票动量策略 (`momentum.py`)

- 每 REBAL=10 交易日检视一次：从"动量>0 且价格>=10元 且有200日均线数据"里挑过去40日涨幅最高（跳最近5日）的1只满仓买入
- 买入不需站上均线；持仓每日检查离场：跌破200日均线(趋势破坏)或从持仓峰值回撤35%卖出，不在股间频繁换仓
- min_px=10 过滤低价炒作票（只用于买入池，不用于离场判断——持仓跌破10元不强平）
- 回测区间 2020-01 ~ 2026-07，基准沪深300，成本万三双向+印花税万五，单票100%
- **结果：年化 33.1% vs 基准 1.5%，总收益 510.1%，夏普 0.72，回撤 68.6%，12 笔胜率 58.3%**（回撤大是满仓单票固有，2020-2021 两笔 -35%/-37% 是回撤主要来源；收益主要靠 2025 一笔 +310% 的长牛股）
- 踩坑：横截面动量每日/月频换仓追最热票必亏（2020-2026 全参数下 -91%~-100%）；改成"买入一次持有到趋势破坏/止损"才有效
- 实验脚本 `momentum_variants.py`（run() 是 momentum.py 的参数化版，用于 sweep，改配置后与 momentum.py 结果必须一致）

## v2 威科夫吸筹策略 (`wyckoff.py`)

- 文章纪律：跌透(250日回撤≥30%)+横盘(60日振幅≤30%) → 吸筹三连(放量下跌/缩量反弹/大幅缩量测试) → 放量突破阳线买入
- 参数经 sweep 调优：TEST_VOL=1.0（0.6→1.0 年化 -1.4%→13.6%），其余默认
- 止损：测试低点×0.95 或亏 8%；持仓看放量滞涨(DIST_VOL)减半仓→清仓，天量(HUGE_VOL)清仓
- 数据缓存 `cache_wyckoff/stocks/{code}.json`（后复权日线，2016起，707只）；baostock socket 非线程安全必须串行拉取
- 回测区间 2018-01 ~ 2026-07，基准沪深300，交易成本万三双向+印花税万五，单票≤20%
- **结果：年化 13.6% vs 基准 1.5%，总收益 124.4%，夏普 0.58，回撤 26.1%，103 笔胜率 53.4%**（收益分散，去掉 top3 赢家每笔均值仍正）
- 扫描脚本：`wyckoff_sweep.py`(单参数) / `wyckoff_joint.py`(联合网格，发现 TEST_VOL=1.0 组合样本少不稳健已弃用)

## 运行 v2

```bash
source .venv/bin/activate && python3 -u wyckoff.py
```

首次运行约 7-8 分钟（串行拉取707只日线），缓存后秒出。

## 运行 v3

```bash
source .venv/bin/activate && python3 -u momentum.py
```

复用 v2 缓存，秒出。

## 运行 v4

```bash
source .venv/bin/activate && python3 -u etf_timing.py
```

读本地缓存，秒出。

## 运行 v5

```bash
source .venv/bin/activate && python3 -u etf_rotation.py
```

读本地缓存，秒出。

## v1 回测策略 (`backtest.py`)

- PE 10年分位：买入 < 30%，卖出 > 60%
- 月频调仓，最多 5 只，等权重
- 沪深300+中证500成分股，排除行业/ST，不足10年数据排除
- 闲置现金 2% 年化（ETF 510300 因数据不足 fallback）
- 交易成本：万三双向 + 卖出印花税万五
- 回测区间 2020-01 ~ 2026-07，基准沪深300
- 数据缓存至 `cache_bt/`（独立于 screener 的 `cache/`）

## 输出

### v0 screener
- CSV (`result_YYYYMMDD_HHMMSS.csv`)
- HTML (`result_YYYYMMDD_HHMMSS.html`): PE/PB 分位散点图 + 分位数分布直方图

### v1 backtest
- CSV (`bt_result_YYYYMMDD_HHMMSS.csv`): 策略净值+HS300 日数据
- HTML (`bt_result_YYYYMMDD_HHMMSS.html`): 净值曲线+年度收益+指标表

### v4 etf_timing
- CSV (`etf_result_YYYYMMDD_HHMMSS.csv`): 策略净值+HS300 日数据
- HTML (`etf_result_YYYYMMDD_HHMMSS.html`): 净值曲线+年度收益+持仓状态与买卖信号+指标表

### v5 etf_rotation
- CSV (`etf_rot_result_YYYYMMDD_HHMMSS.csv`): 策略净值+HS300 日数据
- HTML (`etf_rot_result_YYYYMMDD_HHMMSS.html`): 净值曲线+年度收益+持仓标的甘特图+指标表

## 技术栈

Python, akshare(指数成分), baostock(PE/PB历史/行业分类/股票名称/指数行情), pandas, plotly

## 当前筛选条件(v0 硬编码)

- PE 5年分位 < 50% AND PB 5年分位 < 50%
- 沪深300 OR 中证500 成分股（满足其一即可）
- 排除行业：银行/煤炭/石油石化/有色金属/房地产（按 CSRC 分类）
- 排除 ST
- PE 5年分位升序排列

## 输出

- CSV (`result_YYYYMMDD_HHMMSS.csv`)
- HTML 可视化 (`result_YYYYMMDD_HHMMSS.html`): PE/PB 分位散点图 + 分位数分布直方图

## 技术栈

Python, akshare(指数成分), baostock(PE/PB历史/行业分类/股票名称), pandas, plotly

## 运行

```bash
source .venv/bin/activate && python screener.py
```

首次运行约3分钟（获取800只成分股5年PE/PB历史，结果缓存至 cache/ 目录），后续秒出。

### v1 backtest

```bash
source .venv/bin/activate && python backtest.py
```

首次运行约7分钟（获取707只股票10年PE/价格日频数据，缓存至 `cache_bt/`），后续秒出（信号计算 1~2 分钟）。

## 人机交互

随对话主动更新 AGENTS.md
