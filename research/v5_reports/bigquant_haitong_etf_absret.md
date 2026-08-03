通过ETF轮动的绝对收益策略-海通证券-20200528 - BigQuant量化交易
body,
    html {
      margin: 0;
      padding: 0;
    }

    body {
      display: flex;
      width: 100%;
      height: 100%;
    }

    #root {
      flex: 1;
      min-height: 100%;
      width: 100%;
    }
if (window.location.hostname !== 'bigquant.com') {
        var s = document.createElement('style');
        s.textContent = '#root>.page{display:none}';
        document.head.appendChild(s);
      }

logo

首页
编写策略
数据平台
策略社区
我的策略
宽客学院
知识库

免费注册
登录

BigQuant使用文档

BigQuant 2026年度私享会

精华帖子

量化数据分析

AI量化知识树

精品课程

研报&论文

公告通知

问答交流

比赛讨论

平台建议

策略分享

旗舰版会员

历史文档

研报&论文

# 
通过ETF轮动的绝对收益策略-海通证券-20200528

由qxiao创建，最终由qxiao
更新于2022-10-09 08:53
被浏览 115 用户

## 
摘要

本篇报告主要研究如何利用行业轮动模型，以及市场上丰富的行业与主题ETF产品，通过对冲手段或者股债配置的手段，实现绝对收益。

当前市场上股票型ETF产品合计252只，其中140只为行业或主题型，数量占比高达55%，规模占比接近50%，从近半年的成交来看，行业与主题型ETF日均成交占股票型ETF成交的58%。无论从总规模、数量还是产品流动性和活跃度来看，行业与主题型产品都在股票型ETF中占据了非常重要的地位。

在30个中信一级行业中，挑选出14个有ETF匹配的行业指数构建轮动策略。这14个行业市值占比70%，收入占比50%，利润占比超过70%。贡献了全样本池中大部分的利润，且从规模方面也能够代表市场主流品种。与14个指数对应的ETF合计57只，规模占行业与主题ETF全样本池50%，成交占比超过80%，是市场热点的代表。

ETF轮动组合相对等权基准池，年化超额收益14%，月度胜率66%，信息比1.51。自2013年以来均跑赢基准，年度胜率100%

轮动组合相对沪深300与中证500，年化可以分别获取17%、16%的超额收益。由于中证500的行业分布相对分散，相对中证500的胜率、信息比更高，分别为64%、1.44

构建基于沪深300和中证500的动态基准，策略相对动态基准年化超额收益21%，月度胜率64%，信息比1.31

将ETF轮动组合作为权益资产，构建根据择时观点进行动态调整的股债再平衡策略，年化收益率为11.19%，夏普比率和calmar比率分别为1.85和1.09。加入择时观点的股债风险平价策略，年化收益率为8.46%，夏普比率和calmar比率分别为2.81和1.77

持有ETF轮动组合作为多头资产，动态选择沪深300或者中证500期货进行对冲，2013年2月以来，对冲策略的年化收益率为9.63%，夏普比率和calmar比率分别为0.87和0.45。剔除2015年7月至2016年股指期货深度贴水时段之后，对冲策略的年化收益率为13.19%，夏普比率和calmar比率分别为1.43和1.10，每个年份均能取得正收益

风险提示：模型误设风险、因子失效风险、流动性风险

## 
正文

/wiki/static/upload/31/31284f66-41ee-4368-a1d0-f0cbed84aa31.pdf

\

{link}
window.env = {"URL":"https://bigquant.com/wiki","AWS_S3_UPLOAD_BUCKET_URL":"","AWS_S3_ACCELERATE_URL":"","CDN_URL":"//cdn.bigquant.com/wiki","COLLABORATION_URL":"wss://bigquant.com/wiki","ENVIRONMENT":"production","MAXIMUM_IMPORT_SIZE":100000000,"SUBDOMAINS_ENABLED":false,"PDF_EXPORT_ENABLED":false,"DEFAULT_LANGUAGE":"zh_CN","EMAIL_ENABLED":false,"GOOGLE_ANALYTICS_ID":"G-B58V018K6L","APP_NAME":"BigQuant量化交易","BASE_URL":"/wiki","HOMEWORK_COLLECTION_ID":"3b45bdb9-14d2-4bf5-9705-839f575b91b4","SHARED_COLLECTION_IDS":["e3bd6ee2-2b66-465f-9125-cc51deba9b1b"],"analytics":{},"SEO_ENABLED":true,"SEO_DEFAULT_TITLE":"BigQuant量化交易","SEO_DEFAULT_DESCRIPTION":"BigQuant人工智能AI量化交易知识库提供AI量化交易入门知识点、股票期货金融、量化交易策略开发常见问题、股票量化交易平台基础功能使用等基础知识点，帮助Quant宽客和量化投资者无门槛的使用AI量化","SEO_DEFAULT_KEYWORDS":"量化交易知识点,量化交易基础知识"};
if (
      window.localStorage &&
      window.localStorage.getItem("theme") === "dark"
    ) {
      var color = "#111319";
      document.querySelector("#root").style.background = color;
      document
        .querySelector('meta[name="theme-color"]')
        .setAttribute("content", color);
    }