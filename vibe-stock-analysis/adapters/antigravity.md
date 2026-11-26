# 在 Google Antigravity 中使用

## 安装

解压发布包，保留 `vibe-stock-analysis` 目录名及完整内容。按 [README](../README.md) 准备 Python 3.10+。工作区安装路径与 Codex 相同。若两个宿主使用同一工作区，保留一份 Skill 即可。

| 使用范围 | 将完整目录放在 |
| --- | --- |
| 该工作区的 Antigravity App、CLI 和 IDE | `<workspace-root>/.agents/skills/vibe-stock-analysis/` |
| 本机所有工作区，App、CLI 和 IDE 共用 | `~/.gemini/config/skills/vibe-stock-analysis/` |

工作区路径指你在 App 项目中添加的本地文件夹、CLI 启动目录或 IDE 打开的工作区。App 和 CLI 会从相应工作区向上扫描至 Git 仓库根目录。使用当前推荐的 `.agents/`。遇到同名 Skill，按当前客户端的优先级加载，请先检查目标目录。 [Antigravity Skills 官方文档](https://antigravity.google/docs/skills) · [发现机制](https://antigravity.google/docs/customization-discovery)

## 调用和验证

- 在 **App** 打开含该目录的本机项目。在「设置 → 项目 → 该项目 → 自定义 → 技能」查找 `vibe-stock-analysis`。在该项目中开启新会话，输入「分析一下谷歌最新财报对股价的影响」，或在提示框中用 `/vibe-stock-analysis` 选中 Skill。若列表未显示，可从会话的工具执行记录核对客户端是否读取 `SKILL.md`。 [Antigravity Skills 官方文档](https://antigravity.google/docs/skills) · [发现机制](https://antigravity.google/docs/customization-discovery)
- 使用 **CLI** 时，在工作区运行 `agy`，用 `/skills` 确认已加载，再直接提问或选择 `/vibe-stock-analysis`。安装后可开新会话或执行 `/skills reload` 更新命令列表。 [Antigravity Skills 官方文档](https://antigravity.google/docs/skills) · [发现机制](https://antigravity.google/docs/customization-discovery)
- 使用**独立 IDE** 时，打开工作区，在 Agent 面板的 Customizations 菜单检查活跃 Skill，然后用自然语言提问。 [Antigravity Skills 官方文档](https://antigravity.google/docs/skills)

可以在同一工作区终端执行 `python3 .agents/skills/vibe-stock-analysis/scripts/stock.py quote NVDA` 核对数据访问，Windows 可用 `py -3`。Antigravity 使用当前选择的模型，Python 脚本按终端与沙箱权限读取本地文件和金融数据。在线数据需要对相关端点的 HTTPS 访问。需要批准时，为当前会话审批相关脚本或请求。 [Antigravity 权限文档](https://antigravity.google/docs/secure-mode)
