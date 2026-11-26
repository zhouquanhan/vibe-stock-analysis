# vibe-stock-analysis

**用你已经在用的 Agent 做个股研究，沿用宿主现有的模型和使用额度。**

`vibe-stock-analysis` 将 Vibe-Trading 的部分个股研究工具和工作流程移植为一个可独立运行的 [Agent Skill](https://agentskills.io/specification)。安装到 OpenAI Codex、Google Antigravity 或其他支持 Agent Skills 的客户端后，你可以直接问「英伟达现在的估值合理吗？」或「帮我看看微软的技术面」。宿主按问题调用 Python 工具，获取可追溯的行情、财报和计算结果，再用自己的模型撰写分析。

## 省去哪些安装和费用

- **省去部署 Vibe-Trading 的步骤。** 如果你的需求是研究个股，复制 Skill 目录并准备 Python 即可。Vibe-Trading 的后端、前端和服务配置可以省掉。
- **省去在另一个项目里配置模型。** Skill 使用你在宿主里已经登录的账号和选定的模型。Python 工具负责取行情、读取财报和计算指标，接入时无需另外申请、填写或保管大模型 API Key。
- **省去一层独立的模型 API 账单。** 你可以用 Codex 或 Antigravity 当前账号已有的模型额度完成研究。Skill 自身无需额外向模型 API 发起按量计费的请求。宿主的原有付费方式、用量上限和超额规则照常适用。使用自带 API Key 的宿主仍按原先的方式计费。

内建公共金融数据源无需另行购买数据套餐。数据覆盖、时效和字段因市场和供应商而异。

## 可以用来做什么

| 想了解什么 | Skill 可以提供什么 |
| --- | --- |
| 现在多少钱，最近涨跌如何？ | 带市场、币种、时间和时效说明的报价、历史 K 线、不同时间窗口表现及基准对比。 |
| 股价处在什么位置？ | MA、EMA、MACD、RSI、KDJ、布林带、ATR、成交量变化、支撑压力位、波动率和最大回撤。 |
| 财报有何变化，估值合理吗？ | 财务报表、盈利和现金流、PE/PB/PS、同业对照、符合条件的历史估值采样，以及新闻、SEC 公告和美国宏观数据。 |
| 一条简单交易规则过去表现如何？ | 单股 SMA/EMA 均线策略回测，可设置成本，并与同一时期的买入持有比较。 |

查询报价时只取必要的行情。做综合研究时，宿主可以把价格、基本面和事件放在一起分析，并给出有条件的情景判断。工具输出包含数据来源、时间、单位和缺失信息。遇到网络异常会尝试可用的备用来源。 [查看真实 NVDA 示例](examples/NVDA-2026-11-25.md)

## 新手安装和使用

### 1. 准备 Python 和 Skill 文件

在终端输入 `python3 --version`。Windows PowerShell 输入 `py -3 --version`。版本应为 3.10 或更新。若系统找不到命令，可以先装 Python，再开一个新终端。 [Python 下载](https://www.python.org/downloads/)

在 GitHub 仓库页面选择 **Code → Download ZIP**，或者解压项目提供的分发包。将包含 `SKILL.md`、`scripts/` 和 `references/` 的完整文件夹命名为 `vibe-stock-analysis`。

### 2. 复制到宿主的技能目录

第一次用，建议先装到你准备用来研究股票的本地项目。Codex 和 Antigravity 共用同一个工作区路径。

```text
你的项目/
└── .agents/
    └── skills/
        └── vibe-stock-analysis/
            ├── SKILL.md
            ├── scripts/
            └── references/
```

缺少 `.agents/skills/` 时可手动创建。在 macOS 的 Finder 中，按 `⌘⇧.` 可以切换隐藏目录的显示。希望在所有本地项目中使用时，也可以选择对应宿主的个人目录，再复制一份完整的 Skill 文件夹到该位置。

| 安装范围 | 位置 | 详细步骤 |
| --- | --- | --- |
| 仅当前项目，Codex 和 Antigravity 通用 | `<项目根目录>/.agents/skills/vibe-stock-analysis/` | [通用接入](adapters/generic.md) |
| Codex 个人安装 | `~/.agents/skills/vibe-stock-analysis/` | [Codex 安装指南](adapters/codex.md) |
| Antigravity 个人安装 | `~/.gemini/config/skills/vibe-stock-analysis/` | [Antigravity 安装指南](adapters/antigravity.md) |

### 3. 新开会话，问第一只股票

用 Codex 或 Antigravity 打开项目，新建一个本机会话。先试一句短问题，再根据需要增加范围。

- 「英伟达最近一笔报价是多少？告诉我报价时间和来源。」
- 「帮我看看微软的技术面，重点看近三个月、均线和最大回撤。」
- 「结合最新财报、估值和技术面分析英伟达，和 AMD 做对比。」
- 「用英伟达过去两年的日线测试 20/50 日均线策略，每次买卖各计 10 个基点成本。」

Codex 可以用 `$vibe-stock-analysis` 显式调用。Antigravity 可以在「自定义 → 已安装」查找个人安装的 Skill，或输入 `/vibe-stock-analysis`。联网分析需要宿主允许 Python 访问金融数据接口。

### 4. 可选，在终端检查行情和安装备用支持

工作区安装后，可以从项目根目录运行以下命令。

```bash
python3 .agents/skills/vibe-stock-analysis/scripts/stock.py quote NVDA --pretty
```

Windows PowerShell 可以把 `python3` 换成 `py -3`。返回 JSON 后，先看 `status`，再看报价的 `price`、`as_of` 和 `source`。需要 yfinance 备用源、Windows 时区数据库或平台证书库时，可以在项目根目录创建虚拟环境。

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r .agents/skills/vibe-stock-analysis/requirements.txt
.venv/bin/python .agents/skills/vibe-stock-analysis/scripts/stock.py quote NVDA --pretty
```

Windows PowerShell 用 `py -3 -m venv .venv` 创建环境，后两条命令的解释器改用 `.\.venv\Scripts\python.exe`。个人安装时，将命令里的 `.agents/skills/vibe-stock-analysis` 换成对应的安装目录。如果宿主用的是另一套 Python，可在会话里明确让它用这个虚拟环境的解释器。

## 数据源与命令参考

- 行情默认依次尝试 Yahoo Chart、yfinance、腾讯、新浪和 Stooq。可用 `--providers tencent,sina,stooq` 或 `VIBE_STOCK_MARKET_PROVIDERS` 指定顺序。成交额由支持该字段的行情源返回。
- 如需频繁或共享使用 SEC 接口，可将 `VIBE_STOCK_SEC_USER_AGENT` 设置为项目名和联系邮箱。脚本沿用系统代理和证书设置。
- Python CLI 可独立运行，也支持从文件或标准输入接收 JSON。 [CLI 与数据字段](references/cli.md) · [计算口径](references/research.md) · [测试报告与局限](references/testing.md)

## 致谢与许可

感谢 **HKUDS 团队和所有 Vibe-Trading 贡献者**开放源代码。这项工作建立在他们编写的行情接入、财报解析、技术指标和研究流程之上。本仓库保留了上游版权声明，并记录了复用的具体模块和版本。 [Vibe-Trading 上游项目](https://github.com/HKUDS/Vibe-Trading) · [上游复用清单](references/upstream.md) · [版权声明](NOTICE) · [MIT 许可证](LICENSE)

本 Skill 用于金融研究和历史模拟。示例与回测结果仅供参考。
