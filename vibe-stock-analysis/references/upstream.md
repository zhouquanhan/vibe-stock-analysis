# 上游研究、迁移清单与维护

研究时使用本机 Vibe Trading 0.1.18 的 Python 安装及同版本源码树，源码提交 `3910bad3c9197de6c1fc6f28a3925ee8729c1a65`。另从公开 GitHub 核对主分支提交 `2134f408b1cd715c74fa8c5021f4402d50b00ea8`（0.1.19），逐文件比较许可证、Agent、Skill、行情、基本面、新闻、宏观、量化和 MCP 相关实现。两版许可证一致。上游在 0.1.19 给部分数据读接口引入统一网关，并添加结构化财务研究产物约定。该 Skill 使用独立的只读 CLI 和自己的 JSON 约定。 [本地对应提交](https://github.com/HKUDS/Vibe-Trading/tree/3910bad3c9197de6c1fc6f28a3925ee8729c1a65) · [核对的公开主分支提交](https://github.com/HKUDS/Vibe-Trading/tree/2134f408b1cd715c74fa8c5021f4402d50b00ea8)

## 上游如何完成一项研究

| 层 | 上游作用 | Skill 中的责任方 |
| --- | --- | --- |
| 模型 | 理解问题、组织调用、写策略或解释、整合证据与提出假设。模型供应配置和模型调用由宿主 Agent Loop 接管。 | Codex、Antigravity 等当前所选模型完成推理和报告。 |
| Agent 与提示词 | `agent/src/agent/loop.py` 迭代模型消息和工具调用。 `context.py` 组装系统提示、可用工具和 Skills。 `skills.py` 发现工作流并支持按需读取。 | 开放标准的 `SKILL.md` 和短参考文件提供路由与研究约束。 |
| 可执行工具 | `agent/src/market_data.py`、`agent/src/tools/`、`agent/backtest/loaders/` 提供行情、报表、行业、新闻、技术分析与宏观访问，`agent/backtest/` 负责模拟执行及绩效。 | 独立 CLI 获取有据可查的数据并进行计算。宿主可补查发行人和监管来源。 |
| MCP | `agent/mcp_server.py` 提供 Skill 列表和读取、公开行情、新闻、SEC 文件与财报、行业、检索、回测等工具，并含交易相关工具。协议层调用上游工具注册表与执行器。 | CLI 的 JSON 输入输出即可接入。分发包采用 `SKILL.md` 与 CLI，不包含 MCP 服务。 |

## 复用清单

“移植”表示提取了原代码中的解析、计算或流程逻辑，并适配到独立脚本。新增模块也保留相关上游来源的注明。

| 本项目 | 已复用或移植 | 适配与补充 |
| --- | --- | --- |
| `scripts/market.py` | `agent/backtest/loaders/{yahoo_client,yfinance_loader,tencent_loader,sina_loader,stooq_loader}.py` 的行情格式和路由。 | 独立 Yahoo Chart 与 yfinance，跨源改换，交易地时区、部分 bar、拆股、成交额和诊断。 |
| `scripts/analytics.py` | `agent/src/tools/technical_indicator_tool.py` 的 SMA、EMA、MACD、布林带、量比和数据指纹逻辑。 `agent/backtest/metrics.py` 的起始高水位回撤规则。 | 标准库计算，增加 KDJ、ATR、滚动极值和共同交易日基准。RSI 使用 Wilder 算术均值初始化。新增下一个开盘成交的单标的均线回测。 |
| `scripts/fundamentals.py` | `agent/backtest/loaders/{sec_edgar_client,_fundamental_schema,sec_frames}.py` 的 SEC 标的解析、财务概念优先序和财期识别。 `stock_profile_tool.py` 与 `financial_statements_tool.py` 提供资料字段和财报处理参考。 | 同币种计算、财季/YTD/TTM 对齐、可见日期过滤、Yahoo 公司/财务数据、Nasdaq 分析师预期。 |
| `scripts/context.py` | `agent/src/tools/{sec_filings_tool,stock_news_tool,fred_macro_tool,stock_profile_tool}.py` 的公告、行情新闻和宏观记录整理。 | 直接用 SEC 和 FRED 公开端点，加入新闻降级源与日期字段。 |
| `scripts/valuation.py` | 沿用 `agent/backtest/loaders/valuation_loader.py` 的日期一致性思路，并调用移植后的 SEC 解析代码。 | 按每个完整财报的披露日和可取得的股数估算历史倍数。 |
| `SKILL.md` 和研究参考 | `agent/src/skills/` 下的 `technical-basic`、`financial-statement`、`valuation-model`、`macro-analysis`、`sec-edgar` 和 `research-discipline` 的研究思路。 | 适配标准 Skill 渐进加载、问题分流和两种真实宿主。 |

原始 MIT 许可和版权在 [LICENSE](../LICENSE) 和 [NOTICE](../NOTICE)。上游 Qlib 因子、字体及其他单独授权资源均未纳入该包。

## 功能覆盖

| 能力 | 状态 | 交付内容或覆盖条件 |
| --- | --- | --- |
| 最新价、涨跌、成交量、历史 K 线、短中长期表现、波动、回撤、指数对比 | 已实现 | 行情包含时效标记。成交额、盘中周期、复权和交易所覆盖视源而定。 |
| MA、EMA、MACD、RSI、KDJ、布林带、ATR、量比、支撑压力和技术信号 | 已实现 | 完成的 K 线、本地程序运算。 |
| 营收、利润、同比、盈利能力、资产负债、现金流、基础估值、可比公司 | 已实现 | SEC 覆盖最完整。有可靠窗口和披露时间时建立 TTM。非 SEC 的 Yahoo 财报可能缺开始日、披露日、数据频率或完整序列，缺失保留。 |
| 历史估值、PEG、盘中成交额、分析师共识 | 部分实现 | 历史倍数限可核实的 SEC 美元资产和行情样本。PEG 为供应商前瞻指标。成交额限支持字段，分析师覆盖取决于来源。 |
| 公司新闻、公告、财报指引、宏观、行业、政策与情绪 | 部分实现 | 自动找新闻与 SEC 文件、按需取美国宏观观测；发行人指引、行业、非美国宏观、新闻倾向与政策解读由宿主补充原始资料。无通用自动情绪评分。 |
| 综合与三情景、风险和条件判断 | 已实现 | 宿主按证据、用户问题和期限推理，保留事实、指标和预期区别。 |
| 量化回测 | 部分实现 | 当前可配置单股 SMA/EMA 多头现金策略、成本和同期基线。上游组合、多因子策略、事件驱动和自定义代码引擎尚未移植。 |
| MCP、经纪接口、下单、账户和交易执行 | 未实现 | 发行包只提供研究 CLI 和历史模拟。 |

## 同步上游

1. 在独立的源码副本获取所需上游版本，记录提交及许可证。对照复用清单，查看提供者、字段、单位、行情时间、财期和算法的变化。
2. 小块移植，保留版权和新的上游路径。新源需给出市场和价格/财务口径，维持公开独立运行，并为出错或空响应填写可追踪诊断。
3. 对价格、份额和财务等有破坏性的口径变动更新测试和 JSON 版本。跑全部离线测试，至少实跑一只美股和受影响的非美股或备用源，再在隔离工作区校验宿主触发。
4. 同步更新文档和发行 ZIP。报价、历史和回测示例应作为带时间的固定研究快照，需要刷新时重新对照一手来源。
