# 自用选股器

## 凭据

- GitHub token 等存于仓库根目录 `.env`（已被 .gitignore 忽略，勿提交）。需要调 GitHub API（如触发 workflow_dispatch）时 `source .env` 取 `GITHUB_TOKEN`。
- TUSHARE_TOKEN 在 `.env`

## 目的

根据用户给定筛选条件选出合适的股票代码，并提供排序和可视化功能。

## 版本索引

| 版本 | 脚本 | 定位 | 状态 |
|---|---|---|---|
| v7 | `etf_rot_signal.py` / `paper_trade_signal.py` / `rot_core.py` | 信号调仓回测 + 模拟盘（当前主用）；策略核心（常量+`rotation_sim` 逐日重放）在 `rot_core.py`，两脚本共用防漂移 | 最新 |
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
>
> 一次性分析/探索脚本（扫参、滚动、AB测试、收益拆解等）已归档至 `backup/`，其结论见 [research/conclusions.md](research/conclusions.md)，不再直接运行。

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

> 所有脚本（v0~v7 回测/筛选）的结果 CSV/HTML 统一写入根目录 `output/`（`OUTPUT_DIR = "output"`，脚本自动创建）；模拟盘报告仍走 `docs/`（GitHub Pages）。

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

池子选择规则（2026-08 探索 40 只候选后定稿）：
- **一簇一只**：去孪生（半导体 vs 芯片 0.98 相关，只留半导体；不加入通信/电子/AI/软件/云计算/电池等近亲，互相 0.8~0.98 相关会稀释动量信号+反复切换白付手续费）
- **覆盖不同市场**：A股行业 + 港股(恒生科技) + 商品(黄金)
- **避开尖峰动量**：不加入原油/豆粕/游戏等脉冲商品概念（回测一买就亏）
- **剔除过小**：AUM <10亿 不要（剔除基建/汽车/计算机/家电）
- **剔除高溢价**：QDII 溢价 >3% 不要（剔除美国50+5.9%/日经+4.1%；恒生科技 430亿 溢价≈0 保留）。**例外：纳指ETF(513100) 溢价+12.5% 也保留**，可作底仓备选（2026-08-04 曾作底仓，后因灰犀牛预案换黄金）
- **历史长优先**：上市 <3年 不要（剔除标普生物/标普消费 2024 上市；红利ETF 2007 历史最长）
- **只进不出**：跌下来就删池=追跌反应，是本策略最大回撤来源（5G通信教训）

## 已验证结论（定稿，勿重复实验；细节见 [research/conclusions.md](research/conclusions.md)）

- **评估基线 = 纳指 513100 买入持有**（2015起 20.9% / 2020起 21.9%），不再以沪深300 为参照；策略真实 alpha = 年化 − 同期纳指年化。
- **官方口径**：v7 回测默认起点 **2016-08-11**（动态池首笔买入日，此前仅红利一只入池+长期空仓故截断）。**默认动态池口径**（1.0/45% + 止跌20d8% + 止盈0.8卖0.5）：**19.3%/夏普0.98/回撤26.1%/总收益444.2%**（2026-08-07 定稿止跌+卖半后，旧值 15.4%/0.83/30.3%/298.2% 作废——无止跌时 tp_frac 单调到 1.0，开启止跌后 0.5 全面更优：drop 先接住回撤、半卖保留半仓减少一次入场摩擦）。精选池 2015-01-01 起 **31.3%/1.13/31.6%/2004.3%**（MA200 预热修复后，旧值 22.9% 作废）。模拟盘从 2026-08-03 开户重放，不受 BT_START 影响。
- **alpha 本质**：趋势跟随吃满黄金 2022-2025 大牛+纳指牛市（黄金 518880 单标的贡献 +146.8%），不是分散行业轮动；机制 alpha 诚实估值 **16-18%/0.7-0.8 夏普**，精选池回测含 7~10pp 样本内选池溢价。
- **因子均为毒药**：短周期价量（alpha191 全套）、估值（行业 PE 分位）——任何与 180 日动量混合的因子都稀释收益，ETF 轮动 alpha 全在趋势跟随 → **不再深挖因子**。
- **参数核心**：`mom_gap` 是唯一陡峭旋钮（切换门槛=策略核心）；ma_n 200/220、lookback 180、trail 0.20-0.25 平台峰区；base_w 单调权衡；cooldown/min_mom 不敏感；2015/2020 两窗形态一致 → 非过拟合。
- **底仓**：黄金 518880 是长周期唯一单调稳健的防御（夏普抬/回撤降，只牺牲 ~2pp 年化）；红利底仓的 26.1% 是 2021-2023 时代红利，不可外推。
- **动态池**：持续更新池是正确实盘做法（同簇一只即可吃到行情）；"上市满3年"规则拖累 ~5pp 年化（min0 时 19.6%/0.75）→ 实盘按"上市即可纳入监控+MA200+180 自然约束"，诚实预期 **18-20%/0.75**（含 3 年等待则 15±2%）。
- **高换手教训**：紧止损/短动量对摩擦零免疫（次方平台口径 61% → 我方口径 9.1%），平台"年化上百"= 窗口起点+前视成交+零摩擦叠加。

## 当前实盘定稿 (2026-08-07)

- **默认池 = 动态池**（2026-08-07 更新）：回测与模拟盘默认走 `dynpool.py`（候选=行业池+explore 55 只，规则：上市满3年/AUM≥10亿/**不去重**(2026-08-07 实测: MOM_GAP=1.0 下同簇相关 0.8~0.98 动量差不可能 >100%, 去重冗余; 池 25→48 只年化 14.5→14.8% 换手不变, 官方 48 只口径)/只进不出/**EXCLUDE 脉冲商品(原油501018/嘉实原油160723/豆粕159985/游戏516010)**/**数据落后≥4自然日不入池(停牌)**/**被拒标的不参与轮动(仅输出 admitted 列)**/入池前 NaN；AUM 拉取失败降级跳过）。当前入池 48 只。`rotation_sim` 增加 `tradable` 掩码：停牌日不可买不可卖、持仓价格冻结（2026-08-07 因虚拟盘持仓 501018 停牌教训引入）。主回测 `etf_rot_signal.py` 与模拟盘 `paper_trade_signal.py` 均已默认切换，`paper_trade.incremental_fetch(..., path=)` 支持 explore 缓存增量更新。
- **底仓黄金 518880**（灰犀牛预案：用户判断美国 AI 泡沫+信用崩塌；实测 2022 熊市 −0.2% vs 纳指底仓 −12.8%，砍纳指暴露到仅轮动仓 55%，只牺牲 ~1.7pp 年化）。
- **止跌20d8% + 止盈 TP_HALF=0.8 + TP_FRAC=0.5**（2026-08-07 止跌止盈联合调参定稿）：轮动仓收盘较 20 日前跌 ≥8% 即离场（与 trail 叠加，任一触发），浮盈 80% 卖半落袋、剩余半仓继续跟 trail/drop。止跌开启后 tp_frac 反转——0.5 卖半全面优于 1.0 全卖（drop 先接住回撤、半卖保留半仓减少一次入场摩擦）；tp_half 0.6~0.8 双窗稳定平台。3年滚动 27 窗跑赢纳指率 26%→48%（基线 15.4% 时代）、窗口年化中位 16.6%、夏普中位 0.80；2020 窗 19.0/0.94/21.6%，次选 10d8% 更强（22.5/1.00）。与诚实预期 18-20%/0.75 吻合。
- **手动规则（策略代码不改）**：黄金/轮动仓无需年线手动卖出，轮动仓自动 trail 止损；若轮动仓持有 513100 且纳指跌破年线(MA250)，可手动平该轮动仓，重新站稳且 MA200 上方买回。

## 次方量化 RSRS 策略复刻 (2026-08-06, `backup/cubefang_rsrs.py`)

OCR 参数：RSRS 动量(27日 high/low 回归斜率 z-score [0,3.7]) 或 26日加权斜率 [0,3.6] + 最小持有2天 + 止盈14%(冷却5天) + 止损6%(冷却2天)，4只池（纳指159941/黄金/创业板100/豆粕159985），佣金万1.1/滑点0.1%。数据 `cache_bt/rsrs_test/`（腾讯 qfq 带 high/low）。

| 变体 | 年化 | 夏普 | 回撤 |
|---|---|---|---|
| RSRS 次方口径（同日收盘/万1.1/滑0.1%） | 15.3% | 0.67 | 31.4% |
| 加权斜率次方口径 | 24.1% | 1.02 | 23.6% |
| RSRS 我方口径 | 12.0% | 0.53 | 27.9% |
| 加权斜率我方口径 | 21.4% | 0.90 | 32.1% |
| RSRS 我方口径+0.2%滑点 | **-0.5%** | -0.04 | 42.1% |

**结论：此策略最水口径也仅 24.1%，撑不起"年化上百"——平台展示数字必然是参数继续打磨或别的策略。675次交易的 RSRS 版 +0.2% 滑点直接归零，高换手对摩擦零免疫再次实锤。我方口径最强平台策略（加权斜率 21.4%）仍低于 v7（22.9%），且 v7 只有 8 段持仓/6.5年。**

## 技术栈

Python, akshare(指数成分), baostock(PE/PB历史/行业分类/股票名称/指数行情), pandas, plotly

## 服务器部署（模拟盘）

- 腾讯云东京 2h4g，SSH 主机/用户/key 在仓库根 `.env`（SSH_HOST/SSH_USER/SSH_KEY，`.env` 已被 .gitignore 忽略，不上传）；WSL 下需先把 key `cp` 到 `~/.ssh/ && chmod 600`（/mnt/c 路径 0444 会被拒）。
- 代码目录 `/opt/paper-trade/`（无 .git 副本），compose 项目 `deploy`（`deploy/docker-compose.yml`），容器 `paper-trade` 端口 8077。挂载：卷 `deploy_paper-cache`→`/app/cache_bt`、`deploy_paper-docs`→`/app/docs`、bind `deploy/state/paper_signal_state.json`→`/app/paper_signal_state.json`。Dockerfile 需 COPY 根目录 `paper_signal_state.json`。
- 更新流程（**rsync 服务器端 Permission denied 不可用，用 tar over ssh**）：本地 `tar czf - --exclude .venv --exclude .git --exclude docs --exclude output --exclude paper_signal_state.json --exclude backup . | ssh ... 'rm -rf /root/paper-rsync && mkdir -p /root/paper-rsync && tar xzf - -C /root/paper-rsync'` → 服务器 `cp -a /opt/paper-trade /root/paper-trade-bak-$(date +%m%d)`、`cp -a /opt/paper-trade/deploy/state /root/keep-state` → 覆盖 `/opt/paper-trade/` 并恢复 `deploy/state/` → `docker stop paper-trade` 后 `cp -a /opt/paper-trade/cache_bt/. /var/lib/docker/volumes/deploy_paper-cache/_data/` → `cp deploy/state/paper_signal_state.json /opt/paper-trade/` → `docker compose build && docker compose up -d`（工作目录 deploy/）→ `docker logs paper-trade --tail` 验证。
- 报告验证：服务器生成的 index.html 中文标题是 **unicode_escape 编码**，grep 中文会误判；用 `re.search(r'"text":"(v7.*?)"', html)` + `decode('unicode_escape')` 验证。
- 服务器另有 opencode server（/root/AGENTS.md），其 AGENTS.md 注明「本机只作承载，勿当业务目标机」，地址见 .env。

## 参考资料

- `research/v5_reports/README.md` — v5 同类量化研报表（西部/湘财/开源/银河/海通/汉斯/利得/策引/聚宽）
- `research/strategy_notes.md` — 各策略扫参/AB测试/数据源坑明细
- `research/conclusions.md` — 回测结论档案（一次性实验，勿重复）

## 知识更新规则

- 新实验结论只追加 `research/conclusions.md`（按日期倒序），AGENTS.md 仅在结论**升级为实盘规则/机制级发现**时改一行。
- AGENTS.md 结论条目若超过 7 条或单条超过 2 行，先把旧的压缩进档案再新增。
