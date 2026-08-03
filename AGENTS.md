# 自用选股器

## 凭据

- GitHub token 等存于仓库根目录 `.env`（已被 .gitignore 忽略，勿提交）。需要调 GitHub API（如触发 workflow_dispatch）时 `source .env` 取 `GITHUB_TOKEN`。

## 目的

根据用户给定筛选条件选出合适的股票代码，并提供排序和可视化功能。

## 版本索引

| 版本 | 脚本 | 定位 | 状态 |
|---|---|---|---|
| v7 | `etf_rot_signal.py` / `paper_trade_signal.py` | 信号调仓回测 + 模拟盘（当前主用） | 最新 |
| v6 | `paper_trade.py` | v5 模拟盘 | 已被 v7 模拟盘替代 |
| v5 | `etf_rotation.py` | 行业ETF动量轮动 | 历史回测 |
| v4 | `etf_timing.py` | ETF牛熊开关择时 | 历史回测 |
| v3 | `momentum.py` | 单票动量 | 历史回测 |
| v2 | `wyckoff.py` | 威科夫吸筹 | 历史回测 |
| v1 | `backtest.py` | PE 分位回测 | 历史回测 |
| v0 | `screener.py` | 基本面筛选 | 历史工具 |

## v7 信号调仓 (`etf_rot_signal.py`)

- **背景**：v5 用 `i % REBAL == 0` 相对起跑日锚定调仓，穷举 40 个相位锚点年化中位仅 6.7%，宣称的 26.6% 恰是最优解 → 相位伪 alpha（细节见 `research/strategy_notes.md`）
- **改法**（参考 `research/v5_reports/`）：每日检视 + 信号过滤，无固定调仓周期
  - 离场：跌破 MA200(trend) 或峰值回撤 TRAIL(trail)
  - 入场：站上 MA200 且动量 > MIN_MOM，空仓买动量最强
  - 切换：候选动量超持仓 MOM_GAP 绝对差才切（策引阈值思想）
  - 冷却期 COOLDOWN：卖出后 N 天内不买回同标的，防损后当日买回被打脸
  - **不接飞刀**：距自身 60 日峰值回撤 ≥ TRAIL 的候选拦停（等回撤收敛再入；模式 E，实验见 `etf_rot_signal_filter.py`，全维度优于无过滤 23.3%→25.1%，滚动回测验证历史路径零干扰）
- 参数：MA_N=200, LOOKBACK=180, MOM_GAP=1.0, MIN_MOM=0.0, TRAIL=0.20, COOLDOWN=20, BASE_W=0.45, BASE_ETF=512890
- **结果（2020-2026, 底仓45%+cd=20）**：年化 24.7%, 夏普 0.98, 回撤 30.5%, 每年跑赢基准；相位无关性已验证（12 起跑日年化 min11.3%/中位18.0%/max24.7%）；加不接飞刀后 26.6%/1.07/24.0%
- **双动量/汉斯评分均失败**（`etf_rot_signal_filter.py` 模式 G/H/I）：给短期动量加权（score=mom+w×mom20）或回归斜率×R² 反而变差（15.7%/17.1%/nan），因为白酒/新能车等最佳入场形态恰是"长动量高+短期刚回调"，短期权重误杀历史大牛（2021 +31.7%→+6.8%）。**结论：长动量选行业 + 短回调入场就该买"短期刚跌"的强势标的，任何短期过滤都干扰**；唯一有效的是 E（不接飞刀）——只拦距自身 60 日峰回撤≥TRAIL 的候选，不动动量排序
- 滚动回测 `etf_rot_signal_rolling.py`：none vs E 窗口统计完全相同（64 窗口、跑赢基准 61、年化中位 28.2%），因 E 只改 2026-07 后的接飞刀行为，历史路径零干扰 → 验证 E 不靠"重写历史"刷高
- 运行产物（`etf_rot_rolling_*`/`etf_rot_signal_result_*`/`etf_rot_signal_trades_*`/`etf_rot_signal_rolling_*`）已加入 .gitignore，不入库

## v7 模拟盘 (`paper_trade_signal.py`)

- **用途**：每日增量更新行情 → 按 v7 规则从起始日幂等重放模拟账户 → 输出持仓/信号/净值曲线，GitHub Pages 展示（https://loss-of-time.github.io/tmp3/）
- 规则与 `etf_rot_signal.py` 回测一致；状态持久化 `paper_signal_state.json`；净值历史 `docs/data/nav.json`；报告 `docs/index.html`（ECharts 渲染，纯 JS 免后端）
- **GitHub Actions `.github/workflows/paper-trade.yml`**：cron `30 7 * * 1-5`（UTC=北京15:30 收盘后）+ workflow_dispatch；跑完 paper-bot 自动 commit `paper_signal_state.json docs cache_bt/etf_industry cache_bt/hs300.json` 并 push，再 deploy-pages
- 数据增量更新：`incremental_fetch()` 从缓存最后日期往前 10 天重拉（保证 qfq 拼接一致）追加，akshare 限流隔 15s 重试 12 次，失败用旧缓存
- 依赖：`pip install akshare baostock pandas plotly`

## v6 模拟盘 (`paper_trade.py`) — 历史

- v5 的模拟盘（规则与 `etf_rotation.py` 回测一致），已被 v7 模拟盘替代，仅作历史参考。状态 `paper_state.json` 与 v7 的 `paper_signal_state.json` 并存不混。

## v5 行业ETF动量轮动 (`etf_rotation.py`)

- 只在调仓日(每 REBAL=40 交易日)检视：从"收盘>MA200牛熊线"的行业ETF里挑过去 LOOKBACK=180 日动量最强的一只持仓；都不站上则空仓吃现金 2%
- 持仓每日检查：跌破 MA200(趋势破坏)或从持仓峰值回撤 20%(移动止损) 即离场；不在板块间频繁切换
- **低波底仓 BASE_W=45%**：恒持红利低波ETF(512890)，剩余 55% 做轮动，稀释单板块暴涨暴跌
- 数据缓存 `cache_bt/etf_industry/{code}.json`（腾讯源 qfq 前复权日线，18 只行业ETF + 红利低波/红利ETF），拉取脚本 `fetch_etf_industry.py`；数据源坑见 `research/strategy_notes.md`
- 回测区间 2020-01 ~ 2026-07，基准沪深300，成本万三双向
- **结果（18只池）：年化 26.6% vs 基准 1.5%，总收益 345.6%，夏普 0.90，回撤 22.2%**；AB 测试/扫参/稳定性见 `research/strategy_notes.md`

## v4 ETF 牛熊开关择时 (`etf_timing.py`)

- 逻辑极简：每日收盘 > MA200 则持有沪深300ETF(510300)（默认 POS_WEIGHT=0.6≈半仓），否则空仓吃现金 2% 年化
- 低频（2020-2026 共 18 次买入信号，约每 4 个月一次），用仓位和空仓控制回撤，适合资金量小/怕回撤的用户
- 数据缓存 `cache_bt/etf510300_qfq.json`（前复权，2012-05-28 ~ 2026-07-31，3446 行），直接读无需网络
- **结果（半仓 0.6）：年化 3.1% vs 基准 1.5%，总收益 21.5%，夏普 0.11，回撤仅 13.2%**
- 参数测试与数据源坑见 `research/strategy_notes.md`

## v3 单票动量 (`momentum.py`)

- 每 REBAL=10 交易日检视一次：从"动量>0 且价格>=10元 且有200日均线数据"里挑过去40日涨幅最高（跳最近5日）的1只满仓买入
- 买入不需站上均线；持仓每日检查离场：跌破200日均线(趋势破坏)或从持仓峰值回撤35%卖出，不在股间频繁换仓
- 回测区间 2020-01 ~ 2026-07，成本万三双向+印花税万五，单票100%
- **结果：年化 33.1% vs 基准 1.5%，总收益 510.1%，夏普 0.72，回撤 68.6%**（回撤大是满仓单票固有）
- 踩坑与实验脚本见 `research/strategy_notes.md`

## v2 威科夫吸筹 (`wyckoff.py`)

- 文章纪律：跌透(250日回撤≥30%)+横盘(60日振幅≤30%) → 吸筹三连(放量下跌/缩量反弹/大幅缩量测试) → 放量突破阳线买入
- 止损：测试低点×0.95 或亏 8%；持仓看放量滞涨(DIST_VOL)减半仓→清仓，天量(HUGE_VOL)清仓
- 数据缓存 `cache_wyckoff/stocks/{code}.json`（后复权日线，2016起，707只；复用于 v3）；baostock socket 非线程安全必须串行拉取
- 回测区间 2018-01 ~ 2026-07，成本万三双向+印花税万五，单票≤20%
- **结果：年化 13.6% vs 基准 1.5%，总收益 124.4%，夏普 0.58，回撤 26.1%，103 笔胜率 53.4%**（收益分散，去掉 top3 赢家每笔均值仍正）

## v1 回测策略 (`backtest.py`)

- PE 10年分位：买入 < 30%，卖出 > 60%；月频调仓，最多 5 只，等权重；沪深300+中证500成分股，排除行业/ST，不足10年数据排除
- 闲置现金 2% 年化；交易成本万三双向 + 卖出印花税万五；数据缓存至 `cache_bt/`（独立于 screener 的 `cache/`）

## v0 screener (`screener.py`)

- **当前筛选条件（硬编码）**：PE 5年分位 < 50% AND PB 5年分位 < 50%；沪深300 OR 中证500 成分股（满足其一即可）；排除行业：银行/煤炭/石油石化/有色金属/房地产（按 CSRC 分类）；排除 ST；PE 5年分位升序排列
- 数据缓存至 `cache/`，首次运行约3分钟（获取800只成分股5年PE/PB历史），后续秒出

## 运行

```bash
source .venv/bin/activate && python3 -u screener.py        # v0 首次约3分钟(拉成分股PE/PB)，缓存后秒出
python3 -u backtest.py        # v1 首次约7分钟(707只10年PE/价格)，缓存后信号计算1~2分钟
python3 -u wyckoff.py         # v2 首次约7-8分钟(串行拉707只日线)，缓存后秒出
python3 -u momentum.py        # v3 复用 v2 缓存，秒出
python3 -u etf_timing.py      # v4 读本地缓存，秒出
python3 -u etf_rotation.py    # v5 读本地缓存，秒出
python3 -u etf_rot_signal.py  # v7 信号调仓回测
python3 -u paper_trade_signal.py  # v7 模拟盘（GitHub Actions 每日自动跑，无需手动）
```

## 输出

### v0 screener
- CSV (`result_YYYYMMDD_HHMMSS.csv`)、HTML (`result_YYYYMMDD_HHMMSS.html`)：PE/PB 分位散点图 + 分位数分布直方图

### v1 backtest
- CSV (`bt_result_YYYYMMDD_HHMMSS.csv`)、HTML (`bt_result_YYYYMMDD_HHMMSS.html`)：净值曲线+年度收益+指标表

### v2 wyckoff / v3 momentum
- CSV (`wyckoff_result`/`momentum_result_*.csv`)+交易明细 (`*_trades_*.csv`)、HTML (`*_result_*.html`)：净值曲线+年度收益+指标表

### v4 etf_timing
- CSV (`etf_result_*.csv`)+信号明细 (`etf_trades_*.csv`)、HTML (`etf_result_*.html`)：净值曲线+年度收益+持仓状态与买卖信号+指标表

### v5 etf_rotation
- CSV (`etf_rot_result_*.csv`)+交易明细 (`etf_rot_trades_*.csv`)、HTML (`etf_rot_result_*.html`)：净值曲线+年度收益+持仓标的甘特图+指标表

### v7 etf_rot_signal
- CSV (`etf_rot_signal_result_*.csv`)+交易明细 (`etf_rot_signal_trades_*.csv`)、HTML (`etf_rot_signal_result_*.html`)：净值曲线+年度收益+指标表

### v6/v7 模拟盘
- 报告 `docs/index.html`（ECharts 渲染净值曲线+轮动持仓区间色条+交易记录表，数据内嵌 JSON）；净值历史 `docs/data/nav.json`；GitHub Pages 展示

## 技术栈

Python, akshare(指数成分), baostock(PE/PB历史/行业分类/股票名称/指数行情), pandas, plotly

## 参考资料

- `research/v5_reports/README.md` — v5 同类量化研报表（西部/湘财/开源/银河/海通/汉斯/利得/策引/聚宽）
- `research/strategy_notes.md` — 各策略扫参/AB测试/数据源坑明细

## 人机交互

随对话主动更新 AGENTS.md
