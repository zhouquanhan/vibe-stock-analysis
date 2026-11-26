# 在 OpenAI Codex 中使用

## 安装

1. 解压发布包，保留 `vibe-stock-analysis/SKILL.md` 和同级的 `scripts/`、`references/` 等目录。按 [README](../README.md) 准备 Python 3.10+，使用你平常登录 Codex 的账号和所选模型。
2. 将完整目录放入工作区根目录的 `.agents/skills/`。个人级安装使用 `~/.agents/skills/`。Codex 从会话当前目录向上扫描至 Git 仓库根目录，个人级目录适用于所有项目。命名冲突时，选择器里可能显示多个同名 Skill。 [Codex Skills 官方文档](https://learn.chatgpt.com/docs/build-skills)
3. 在该工作区启动 Codex CLI 或 IDE 扩展，或者在 ChatGPT 桌面端 Codex 中为本机项目创建新会话。Skill 列表通常自动刷新，未出现时重启客户端后再检查。 [Codex Skills 官方文档](https://learn.chatgpt.com/docs/build-skills)

以下是 macOS/Linux 的工作区安装示例。先进入工作区，将 `skill_source` 换成实际解压目录。目标存在时命令将保留现有文件。

```sh
skill_source='/path/to/unzipped/vibe-stock-analysis'
skill_target='.agents/skills/vibe-stock-analysis'
mkdir -p '.agents/skills'
if [ -e "$skill_target" ] || [ -L "$skill_target" ]; then
  printf '%s\n' '已存在同名 Skill，请先检查目标目录。'
else
  cp -R "$skill_source" "$skill_target"
fi
```

## 调用与验证

- 自然语言询问「帮我看看微软的技术面」。Codex 会按 Skill 的描述判断是否加载完整指令。 [Codex Skills 官方文档](https://learn.chatgpt.com/docs/build-skills)
- 在 CLI 或 IDE 扩展，用 `/skills` 查看已发现的 Skill，或者输入 `$vibe-stock-analysis 帮我分析一下英伟达的股票` 显式调用。在 ChatGPT 桌面端也可从 Skills 面板查看。 [Codex Skills 官方文档](https://learn.chatgpt.com/docs/build-skills)
- 在同一个工作区终端试运行 `python3 .agents/skills/vibe-stock-analysis/scripts/stock.py quote NVDA`。Windows 可用 `py -3`。留意 JSON 中的来源、行情时间和状态。向模型提问时可直接说股票名称，无需输入命令。
- 在线获取行情、财报与新闻需要宿主允许对应脚本访问金融数据源的 HTTPS 端点。遇到沙箱提示时，按会话界面审批本次请求。若工作环境禁止联网，可按 [CLI 参考](../references/cli.md) 使用本地历史快照运行技术分析或回测。
