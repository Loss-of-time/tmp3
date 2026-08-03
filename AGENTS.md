# 自用选股器

## 参考资料：v5 同类量化研报 (`research/v5_reports/`)

收集与 v5 行业ETF轮动策略相似的量化报告，供后续策略迭代参考。PDF 已转 markdown 便于 AI 阅读：

| 文件 | 来源 | 与 v5 关系 |
|---|---|---|
| `xbsec_industry_cta_202502.md` | 西部证券《行业动量策略的CTA思维》 | 最贴近：时间序列趋势跟踪+通道突破，无固定调仓周期；ETF轮动组合年化18% |
| `xcsec_momentum_reversal.pdf/md` | 湘财证券《基于动量和反转的行业轮动策略》 | 动量轮动+市场环境择时(300ETF牛熊开关)，等价 v5 牛熊开关 |
| `kaiyuan_industry_rot_3.0_202412.md` | 开源证券《行业轮动3.0》 | 6模型合成(交易行为/景气度/资金流/筹码/宏观/技术指标)，一级行业+双周频率动量最优 |
| `galaxy_diffusion_etf.pdf/md` | 银河证券《行业轮动模型在行业及主题ETF配置上的应用》 | 行业扩散指数选前6行业 |
| `haitong`→`bigquant_haitong_etf_absret.md` | 海通证券《通过ETF轮动的绝对收益策略》 | 行业动量选行业ETF+对冲/股债再平衡 |
| `hans_momentum_risk.pdf/md` | 汉斯期刊《基于动量与风险优化双重视角的ETF行业轮动》 | 复合动量评分(回归趋势强度)+协方差收缩权重优化 |
| `lide_etf_monthly_202601.pdf/md` | 利得基金 2026-01 ETF轮动月报 | 多维打分的行业轮动模型实践月报 |
| `ceyin_momentum_rotation.md` | 策引《动量轮动策略详解》 | 动量窗口/调仓频率/止损的参数方法论 |
| `joinquant_etf_rotation_10yr.md` | 聚宽帖子 | 散户版 ETF 轮动十年回测，评论区有"挑池子过拟合"警示 |

注意：fxbaogao(发现报告)上的西部/开源全文需付费，本地仅存首页摘要+目录；PDF 直链来自东财/pdf.hanspub.org/iyanbao/bigquant。

## 目的

根据用户给定筛选条件选出合适的股票代码，并提供排序和可视化功能

## 进度

- [x] v0 完成：`screener.py` 单脚本跑通
- [x] v1 回测：`backtest.py` PE 10年分位策略回测
- [x] v2 威科夫吸筹策略：`wyckoff.py` 事件驱动回测
- [x] v3 单票动量策略：`momentum.py` 时间序列动量（资金量小，单票满仓）
- [x] v4 ETF 择时：`etf_timing.py` 牛熊开关（回撤小，可不满仓）
- [x] v5 行业ETF轮动：`etf_rotation.py` 动量轮动+牛熊开关（收益高）
- [x] v6 模拟盘：`paper_trade.py` + GitHub Actions 每日自动运行，GitHub Pages 展示

## v6 GitHub 模拟盘 (`paper_trade.py`)

- **用途**：v5 策略实盘模拟——每日增量更新行情 → 推进模拟账户 → 输出当前持仓/今日信号/净值曲线，GitHub Pages 展示（https://loss-of-time.github.io/tmp3/）
- 模拟账户从**首次运行当天**开始（净值 1.0）：BASE_W=45% 恒持红利低波 + 剩余做轮动，规则与 etf_rotation.py 回测完全一致（MA200/动量180/40日调仓/20%止损）
- 状态持久化 `paper_state.json`（start/持仓/成本/峰值/调仓计数器/净值历史/交易记录）；净值历史 `docs/data/nav.json`；报告 `docs/index.html`（ECharts CDN 渲染净值曲线+轮动持仓区间色条+交易记录表，纯 JS 免后端，数据内嵌 JSON）
- 数据增量更新：`incremental_fetch()` 从缓存最后日期往前 10 天重拉（保证 qfq 拼接一致）追加，akshare 限流隔 15s 重试 12 次，失败用旧缓存；首次运行从最新交易日开始、首日即调仓日
- **GitHub Actions `.github/workflows/paper-trade.yml`**：cron `30 7 * * 1-5`（UTC=北京15:30 收盘后）+ workflow_dispatch；跑完 paper-bot 自动 commit `paper_state.json docs cache_bt/etf_industry` 并 push，再 deploy-pages 到 GitHub Pages
- 依赖：`pip install akshare baostock pandas plotly`（paper_trade.py 顶层经 backtest/etf_rotation import baostock）
- 踩坑：新 push 的 workflow 不会自动注册，需对 workflow 文件做改动再 push 触发扫描
- **首个运行（2026-08-03）**：买入 5G通信(515050)，净值 0.9998（扣佣）
- 仓库：Loss-of-time/tmp3（公开），本地改完 push 到 master 即触发下一次调度


## v5 行业ETF动量轮动 (`etf_rotation.py`)

- 只在调仓日(每 REBAL=40 交易日)检视：从"收盘>MA200牛熊线"的行业ETF里挑过去 LOOKBACK=180 日动量最强的一只持仓；都不站上则空仓吃现金 2%
- 持仓每日检查：跌破 MA200(趋势破坏)或从持仓峰值回撤 20%(移动止损) 即离场，等下一个调仓日再进；不在板块间频繁切换
- **低波底仓 BASE_W=45%**：此比例资金始终持有红利低波ETF(512890)，剩余 55% 做板块轮动。稀释单板块暴涨暴跌（2026-06 半导体 +47% 后 7 月 -33% 全回吐是纯轮动主要回撤来源）
- 数据缓存 `cache_bt/etf_industry/{code}.json`（**腾讯源 qfq 前复权日线**，18 只行业ETF：券商/军工/白酒/医药/半导体/光伏/新能源车/有色/煤炭/传媒/5G/芯片/机器人(562500)/黄金(518880)/化工(516020)/消费(159928)/标普消费(159529)/电力(159611) + 红利低波/红利ETF），拉取脚本 `fetch_etf_industry.py`（**腾讯 web.ifzq.gtimg.cn fqkline 分页**，单页≤640行，end 前移翻页；东财 push2his 2026-08 起持续连接被重置不可用；baostock ETF 历史缺失；新浪不复权会污染动量计算）
- 回测区间 2020-01 ~ 2026-07，基准沪深300，成本万三双向
- **结果（18只池，底仓45%+止损20%）：年化 26.6% vs 基准 1.5%，总收益 345.6%，夏普 0.90，回撤 22.2%，买入 8 次+切换 7 次**（旧 12 只池 20.2%/0.74/27.1%；2026 回撤 20.3%）
- **AB 测试 `etf_rot_abtest.py`（旧12只 vs 新18只池，参数一致）**：新池年化 20.2%→26.6%、夏普 0.74→0.90、回撤 27.1%→22.2%。新增板块中**黄金(518880)、标普消费(159529) 实际被轮动选中**贡献收益；机器人/化工/消费/电力 未触发轮动交易但扩大池子无碍
- 扫参：长动量(120-180日)是关键；短动量(40-60日)高频切换必亏（LB60 默认参数下 -11.2%/回撤 75%），重蹈 v3 追热覆辙；REBAL 越大越稳；MA200 略优于 150/250；底仓 35-55%+止损 20% 一档，再高底仓收益下降
- **稳定性测试 `etf_rot_sweep.py`（18只池，逐参数扰动，默认值★）**：BASE_W 最稳（0.35-0.60 年化 28.6→23.4%、夏普恒 0.90，回撤随底仓单调下降）；TRAIL 0.20-0.25 平台（夏普 0.86-0.90）；敏感参数是 LOOKBACK（峰值 180=26.6%，150 跌到 17.7%、240 跌到 4.8%）、REBAL（峰值 40=26.6%，30 跌到 12%）与 MA_N（峰值 200=26.6%，150/220 跌到 16%）。默认参数位于各参数峰值附近，稳健性可接受，但 LOOKBACK/REBAL/MA_N 依赖峰值更陡需注意过拟合

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

## 运行 v6

```bash
source .venv/bin/activate && python3 -u paper_trade.py
```

增量更新缓存后输出模拟盘状态（网络失败用旧缓存）。每日自动由 GitHub Actions 运行，无需手动。

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
