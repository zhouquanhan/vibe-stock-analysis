# 接入兼容 Agent Skills 标准的宿主

将完整的 `vibe-stock-analysis` 目录复制到宿主指定的 Skill 搜索目录。`SKILL.md` 在这一目录的第一层。保留目录名、子目录和相对路径。Skill 标准规定了文件、元数据和渐进式加载方式，搜索目录和用户界面由各宿主决定。 [Agent Skills 规范](https://agentskills.io/specification) · [客户端实现指南](https://agentskills.io/client-implementation/adding-skills-support)

宿主需要能读取 Skill 文件、运行 Python 3.10+ 并处理标准输出。允许宿主先把 `name`、`description` 和文件位置提供给模型，匹配股票研究任务后再读取完整指令和必要引用。执行工具时把工作目录设为 Skill 目录，或以参数传入脚本的完整路径。Windows 请用 PowerShell 或宿主的进程执行接口，`py -3` 可替换 `python3`。

对于只提供进程执行接口的宿主，可按需注册一个 JSON 输入、JSON 输出的本地工具。以下命令从 Skill 根目录读取 stdin。也可把 `-` 改成 JSON 文件路径。

```sh
python3 scripts/stock.py --json-input -
```

```json
{"action":"quote","symbol":"NVDA"}
```

把进程退出状态和 JSON 输出一起返回模型。应用应检查顶层状态及各数据区块的来源、时间和诊断。在线数据需要 HTTPS。对于隔离宿主，可在有权联网的环境生成合规的行情快照，再导入进行本地指标计算。依赖和环境变量见 [README](../README.md)，请求字段及退出状态见 [CLI 参考](../references/cli.md)。
