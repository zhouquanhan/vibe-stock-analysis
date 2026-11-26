# vibe-stock-analysis

[English](README.en.md) · **简体中文**

**用你已经在用的 Agent 和模型额度做个股研究。**

`vibe-stock-analysis` 把 Vibe-Trading 的部分个股研究工具移植为可独立运行的 Agent Skill。它适用于 OpenAI Codex、Google Antigravity 和其他支持 Agent Skills 的客户端。你可以直接问「英伟达现在的估值合理吗？」或「帮我看看微软的技术面」。[Agent Skills 规范](https://agentskills.io/specification)

安装只需 Python 和 Skill 文件。这样可以省去部署 Vibe-Trading 服务、在第二个项目中配置模型 API Key，以及为这套研究工具另外支付一笔按量计费的模型 API 费用。Python 工具获取数据并计算指标，由用户选用的模型生成分析。使用量计入用户现有的账号、订阅或 API 方案。

## 能做什么

| 研究任务 | 提供的数据与计算 |
| --- | --- |
| 行情与技术面 | 报价、历史 K 线、均线、MACD、RSI、KDJ、布林带、ATR、波动率、回撤和指数对比 |
| 财报与估值 | 财务报表、SEC 公告、PE/PB/PS、同业比较和符合条件的历史估值采样 |
| 事件与回测 | 新闻、美国宏观数据，以及带交易成本和买入持有基线的单股 SMA/EMA 回测 |

工具输出会保留来源、时间、币种和缺失标记。公开行情的覆盖及时效取决于市场与数据源。完整分析流程可参考 [NVDA 综合研究示例](vibe-stock-analysis/examples/NVDA-2026-11-25.md)。

## 安装并开始使用

1. 准备 Python 3.10 或更新版本，下载 [最新 Release 的 Skill ZIP](https://github.com/zhouquanhan/vibe-stock-analysis/releases/latest)。
2. 解压后将完整的 `vibe-stock-analysis/` 复制到项目的 `.agents/skills/`。确认 `SKILL.md` 位于 `.agents/skills/vibe-stock-analysis/SKILL.md`。
3. 在 Codex 或 Antigravity 中打开该项目并新建会话，输入「帮我看看英伟达最新报价，标注时间和来源」。查询在线数据时，请确保终端可以访问公共金融数据接口。

安装后，可以从项目根目录运行以下命令验证报价。

```bash
python3 .agents/skills/vibe-stock-analysis/scripts/stock.py quote NVDA --pretty
```

Windows PowerShell 将 `python3` 换成 `py -3`。个人级安装、可选依赖与其他客户端路径见 [新手安装指南](vibe-stock-analysis/README.md) · [Codex](vibe-stock-analysis/adapters/codex.md) · [Antigravity](vibe-stock-analysis/adapters/antigravity.md) · [通用接入](vibe-stock-analysis/adapters/generic.md)。

## 源码与资料

- [Skill 文件夹](vibe-stock-analysis/) · [SKILL.md](vibe-stock-analysis/SKILL.md) · [Python 工具](vibe-stock-analysis/scripts/) · [测试](vibe-stock-analysis/tests/)
- [CLI 参考](vibe-stock-analysis/references/cli.md) · [测试报告与已知局限](vibe-stock-analysis/references/testing.md) · [上游复用清单](vibe-stock-analysis/references/upstream.md)

## 致谢与许可

感谢 **HKUDS 团队和所有 Vibe-Trading 贡献者**开放源代码。本项目复用了部分行情接入、财报解析、技术计算和研究流程，并保留了上游版权声明。 [Vibe-Trading 上游项目](https://github.com/HKUDS/Vibe-Trading) · [版权声明](vibe-stock-analysis/NOTICE) · [MIT 许可证](vibe-stock-analysis/LICENSE)

本 Skill 用于金融研究与历史模拟，示例与回测仅供参考。
