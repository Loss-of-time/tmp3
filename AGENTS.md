# 自用选股器

## 凭据

- GitHub token 等存于仓库根目录 `.env`（已被 .gitignore 忽略，勿提交）。需要调 GitHub API（如触发 workflow_dispatch）时 `source .env` 取 `GITHUB_TOKEN`。
- TUSHARE_TOKEN 在 `.env`

## 目的

根据用户给定筛选条件选出合适的股票代码，并提供排序和可视化功能。

## 版本索引

| 版本 | 脚本 | 定位 | 状态 |
|---|---|---|---|
| v7 | `etf_rot_signal.py` / `paper_trade_signal.py` | 信号调仓回测 + 模拟盘（当前主用） | 最新 |
| v7实验 | `etf_rot_signal_cyb.py` | v7+v4.1创业板择时混合（入池/拆仓/牛熊门控 A/B/C） | 实验 |
| v6 | `paper_trade.py` | v5 模拟盘 | 已被 v7 模拟盘替代 |
| v5 | `etf_rotation.py` | 行业ETF动量轮动 | 历史回测 |
| v4 | `etf_timing.py` | ETF牛熊开关择时 | 历史回测 |
| v4.1 | `etf_timing_cyb.py` | 创业板ETF牛熊开关 | 历史回测 |
| v3.2 | `momentum_regime.py` | 牛熊切换动量多票 | 历史回测 |
| v3.1 | `momentum_top5.py` | 动量多票(最多5只) | 历史回测 |
| v3 | `momentum.py` | 单票动量 | 历史回测 |
| v2 | `wyckoff.py` | 威科夫吸筹 | 历史回测 |
| v1 | `backtest.py` | PE 分位回测 | 历史回测 |
| v0 | `screener.py` | 基本面筛选 | 历史工具 |

> 各版本策略详解（规则/参数/结果/踩坑）见 [VERSIONS.md](VERSIONS.md)。

## 运行

```bash
source .venv/bin/activate && python3 -u screener.py        # v0 首次约3分钟(拉成分股PE/PB)，缓存后秒出
python3 -u backtest.py        # v1 首次约7分钟(707只10年PE/价格)，缓存后信号计算1~2分钟
python3 -u wyckoff.py         # v2 首次约7-8分钟(串行拉707只日线)，缓存后秒出
python3 -u momentum.py        # v3 复用 v2 缓存，秒出
python3 -u momentum_top5.py   # v3.1 动量多票(最多5只等权)，纯离线，秒出
python3 -u momentum_regime.py # v3.2 牛熊切换动量多票(熊市切红利低波)，纯离线，秒出
python3 -u etf_timing.py      # v4 读本地缓存，秒出
python3 -u etf_timing_cyb.py  # v4.1 创业板ETF 牛熊开关+峰值止损，读本地缓存，秒出
python3 -u etf_rotation.py    # v5 读本地缓存，秒出
python3 -u etf_rot_signal.py  # v7 信号调仓回测
python3 -u etf_rot_signal_cyb.py  # v7实验 v7+v4.1创业板择时混合(入池/拆仓对比)，纯离线，秒出
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

## v7 ETF 池 (23只)

池子由 `fetch_etf_industry.py` 的 ETFS/NEW_ETFS/LOWVOL 定义，缓存 `cache_bt/etf_industry/*.json` 即回测池。

池子选择规则（2026-08 探索 40 只候选后定稿，见 `explore_etfs.py` 与 `cache_bt/etf_explore/`）：
- **一簇一只**：去孪生（半导体 vs 芯片 0.98 相关，只留半导体；不加入通信/电子/AI/软件/云计算/电池等近亲，互相 0.8~0.98 相关会稀释动量信号+反复切换白付手续费）
- **覆盖不同市场**：A股行业 + 港股(恒生科技) + 商品(黄金)
- **避开尖峰动量**：不加入原油/豆粕/游戏等脉冲商品概念（回测一买就亏）
- **剔除过小**：AUM <10亿 不要（剔除基建/汽车/计算机/家电）
- **剔除高溢价**：QDII 溢价 >3% 不要（剔除美国50+5.9%/日经+4.1%；恒生科技 430亿 溢价≈0 保留）。**例外：纳指ETF(513100) 溢价+12.5% 也保留**，作底仓（2026-08-04 起底仓由红利低波 512890 换为 513100）
- **历史长优先**：上市 <3年 不要（剔除标普生物/标普消费 2024 上市；红利ETF 2007 历史最长）
- **只进不出**：跌下来就删池=追跌反应，是本策略最大回撤来源（5G通信教训）

> 探索结论：全部 40 候选一起加 → 年化 25.8%→16.0%；精选每簇一只 → 与基准完全一致（新簇在历史窗口从未夺冠，是纯未来期权）。过滤后 22 只池年化 26.1%/夏普1.05/回撤22.1%（优于过滤前 25.8%/22.3%）。规模/溢价数据源：akshare `fund_etf_spot_em`（东财，2026-08 已恢复）。

## 池稳定性验证 (2026-08-04)

- **滚动回测** `etf_rot_signal_rolling.py`（窗口252日/步21日，基准纳指）：65 窗口，跑赢纳指 65%，窗口年化中位 27.6%/均值 28.2%，夏普中位 1.55，回撤中位 10.8% → 无起跑日相位敏感。none vs E(不接飞刀) 过滤 64/65 窗口完全一致 → 当前池无接飞刀问题。
- **参数敏感性（22只池/红利低波底仓）** `etf_rot_signal_sweep.py`（v7 版逐参数扰动）：唯一陡峭旋钮是 `mom_gap`（0→7.7%、0.5→12.6%、1.0→26.1%，即切换门槛是策略核心）；其余呈平台：ma_n 200/220、lookback 150-180、trail 0.20-0.25 为峰区；base_w 单调权衡（低底仓高收益高回撤）；cooldown/min_mom 不敏感。26.1% 非单点过拟合。
- **底仓换纳指ETF(513100) 复扫（2026-08-04 夜）**：默认 29.8%/夏普1.16/回撤22.1%（vs 红利低波底仓 26.1%/1.05/22.1%）。敏感性形态不变：mom_gap 仍最陡（0→15.0%、1.0→29.8%）；ma_n 200/220 峰区；lookback 180 最优；trail 0.20-0.25 平台；base_w 单调权衡；cooldown/min_mom 不敏感。默认点邻域（ma_n220/trail0.25/mom_gap1.5/base_w0.40~0.50）均 28.4~30.4%/夏普1.12~1.16 → 非尖峰过拟合。对比 IXIC 指数作底仓（28.4%/回撤25.6%）不如真 ETF 513100（29.8%/22.1%）。

## 技术栈

Python, akshare(指数成分), baostock(PE/PB历史/行业分类/股票名称/指数行情), pandas, plotly

## 参考资料

- `research/v5_reports/README.md` — v5 同类量化研报表（西部/湘财/开源/银河/海通/汉斯/利得/策引/聚宽）
- `research/strategy_notes.md` — 各策略扫参/AB测试/数据源坑明细

## 人机交互

随对话主动更新 AGENTS.md
