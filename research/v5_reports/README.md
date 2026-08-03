# v5 同类量化研报

收集与 v5 行业ETF轮动策略相似的量化报告，供后续策略迭代参考。PDF 已转 markdown 便于 AI 阅读：

| 文件 | 来源 | 与 v5 关系 |
|---|---|---|
| `xbsec_industry_cta_202502.md` | 西部证券《行业动量策略的CTA思维》 | 最贴近：时间序列趋势跟踪+通道突破，无固定调仓周期；ETF轮动组合年化18% |
| `xcsec_momentum_reversal.pdf/md` | 湘财证券《基于动量和反转的行业轮动策略》 | 动量轮动+市场环境择时(300ETF牛熊开关)，等价 v5 牛熊开关 |
| `kaiyuan_industry_rot_3.0_202412.md` | 开源证券《行业轮动3.0》 | 6模型合成(交易行为/景气度/资金流/筹码/宏观/技术指标)，一级行业+双周频率动量最优 |
| `galaxy_diffusion_etf.pdf/md` | 银河证券《行业轮动模型在行业及主题ETF配置上的应用》 | 行业扩散指数选前6行业 |
| `bigquant_haitong_etf_absret.md` | 海通证券《通过ETF轮动的绝对收益策略》 | 行业动量选行业ETF+对冲/股债再平衡 |
| `hans_momentum_risk.pdf/md` | 汉斯期刊《基于动量与风险优化双重视角的ETF行业轮动》 | 复合动量评分(回归趋势强度)+协方差收缩权重优化 |
| `lide_etf_monthly_202601.pdf/md` | 利得基金 2026-01 ETF轮动月报 | 多维打分的行业轮动模型实践月报 |
| `ceyin_momentum_rotation.md` | 策引《动量轮动策略详解》 | 动量窗口/调仓频率/止损的参数方法论 |
| `joinquant_etf_rotation_10yr.md` | 聚宽帖子 | 散户版 ETF 轮动十年回测，评论区有"挑池子过拟合"警示 |

注意：fxbaogao(发现报告)上的西部/开源全文需付费，本地仅存首页摘要+目录；PDF 直链来自东财/pdf.hanspub.org/iyanbao/bigquant。
