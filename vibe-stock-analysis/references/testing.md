# 测试报告

测试在 2026-11-25 完成。执行环境为 Apple Silicon macOS、Python 3.14.6。本机 Vibe Trading 和已安装技能继续使用原位置。宿主验证使用独立的测试工作区与临时 Skill 副本。

## 实测结果

| 要求 | 验证及结果 |
| --- | --- |
| 最新股票行情 | NVDA 的 Yahoo Chart 及 yfinance 实时读取成功。时效无法确认的报价标为 `freshness_unverified`。腾讯独立实测 NVDA 盘中成交额。另成功读取港股、A 股、指数和英国便士报价标的。 |
| 历史行情 | NVDA 2 年窗口 502 根日线，501 根已完成，11 月 25 日盘中 bar 单独标记。NVDA 5 年 Yahoo 与 yfinance 两条路径均返回 1,255 根，识别 2024-06-10 的 10 比 1 拆股。历史截至 2026-11-24 返回该日收盘 155.38 美元。 |
| 技术指标 | 以恒定序列、Wilder 参考值、跳空、空成交量及未完成 K 线验证计算。共同交易日比较与回撤按独立样例核验。 |
| 基本面 | NVDA 的 SEC XBRL 与提交记录、Yahoo 统计和分析师数据可用。腾讯 0700.HK 的 CNY 财务报表包含 4 个年度和 2 个中期样本。实测 SNOW 的 SEC 代码备用检索和 NVDA 的 Nasdaq EPS 预测备用端点。 |
| 异常与备用 | 离线模拟 yfinance 超时、HTTP 429、空响应与无数据窗口。真实强制回退链中 Stooq 返回拦截页面，之后腾讯给出 NVDA 报价，新浪给出最后一个收盘的日线。独立源 11 月 24 日的收盘价一致，成交量存在供应商差异。 |
| Skill 格式与加载 | 标准前置元数据通过 OpenAI Skill Creator 验证器。Codex app-server `skills/list` 在独立 Git 工作区识别项目级 Skill，返回启用状态与零解析错误。 |
| Codex | macOS 本机 CLI `0.171.0-alpha.13.2`。用中文提问 NVDA 技术面，提示词不含 Skill 名。行为记录显示先读取 `SKILL.md`，再运行 CLI（退出码 0）。结果与直接运算一致。 |
| Antigravity | macOS 本机 **App 2.35.1**。同一中文提示通过会话调用，工具记录确认先读取 `SKILL.md`，随后运行 Python CLI。结果与直接运算一致。测试前设置页未列出该 Skill，因此以实际会话读文件和执行记录为依据。 |
| 真实 NVDA 综合分析 | 完整 CLI 取到 NVDA 及 S&P 500 行情、AMD 与 AVGO、SEC、新闻及 FRED 数据，输出六个有依据的历史估值采样点。结合正式公告原文完成报告和备份回测。[研究示例](../examples/NVDA-2026-11-25.md) |
| 回测 | 实取 NVDA 两年窗口，SMA20/50、每边 10 bps。下一开盘执行，评估 451 根完成日线，累计收益 +15.09%。离线测试另核验费用、无前视信号及未确认复权的默认拒绝。 |
| 依赖与独立运行 | 新建独立虚拟环境，由 `requirements.txt` 安装成功，`pip check` 通过。实测 yfinance 1.8.0、tzdata 2026.6 与系统证书依赖 truststore。全量研究验收返回 `ok`。无第三方包的独立环境也通过离线测试，并实取腾讯报价、新浪 128 根日线、SEC 与 FRED 的三个宏观序列。该备用行情没有拆股记录，历史估值明确返回不可用。 |

两个宿主的兼容测试使用同一份 67 根模拟日线（仅为宿主加载与执行验证）。问题给出本地数据文件，让两个宿主自动识别 Skill 并调用技术指标工具。报告 RSI14 均为 **49.322159437701394**，最后一根日期为 **2025-04-04**。测试用已登录的宿主模型完成，数据计算离线执行。两个宿主均使用更新后的 Skill 重测。

## 本地复跑

在 Skill 目录运行。Windows PowerShell 用 `$env:PYTHONPATH='scripts'` 后执行 `py -3 -m unittest discover -s tests -v`。

```bash
PYTHONPATH=scripts python3 -m unittest discover -s tests -v
python3 scripts/stock.py quote NVDA --providers yfinance,tencent
python3 scripts/stock.py history NVDA --period 6mo --as-of 2026-11-24 --providers yahoo
python3 scripts/stock.py research NVDA --period 2y --benchmark '^GSPC' --peers AMD,AVGO
python3 scripts/stock.py research NVDA --period 6mo --providers tencent,sina --benchmark none --no-yahoo --no-news
```

离线套件覆盖 35 项测试。真实端点会更新，复跑应比较来源、日期、数据是否有效、当前部分 bar 处理和异常回退，不应期待重现采集期间的盘中价格。

## 未实机覆盖和已知局限

- Antigravity CLI、独立 IDE、App 显式斜杠调用、Windows/Linux 的真实客户端，以及其他独立 Agent Harness，依据官方文档或标准完成适配。它们未执行实机测试。宿主选择 Skills 的表现受当前模型、会话和权限影响。
- 免费行情存在延迟、历史窗口限制、字段差异、停牌数据缺口、改版和限流。日线日期使用交易地日期。周线和月线在周期切换后才视为完成。部分市场的验证依赖数据源公布的交易时段。
- 财务、币种和申报延迟因交易所和发行人而异。非 SEC 当前财报可缺披露时间，历史估值仅在同币种和有可靠拆股/股数证据的 SEC 样本计算。当前 FRED 系列可能修订，使用观测日截断不会恢复历史发布版本。
- 新闻为公开标题索引，行业、非美国宏观、监管影响、可比公司选择和未结构化指引需要宿主判断并回查。估值比率和分析师预期各有自己的分母期和日期。
- 回测限单标的、两种均线设定和可选成本。可快速检验历史行为，外推到未来前需另外检验样本外表现、滑点、容量及完整交易成本。
