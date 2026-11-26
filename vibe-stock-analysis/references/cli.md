# CLI 与数据约定

从 Skill 目录运行 `python3 scripts/stock.py --help` 或 `python3 scripts/stock.py <action> --help`。Windows 可改用 `py -3`。以下示例使用 Bash 或 zsh。

```bash
python3 scripts/stock.py quote NVDA
python3 scripts/stock.py quote 0700.HK --providers tencent
python3 scripts/stock.py history 600519.SS --period 6mo --interval 1d --max-bars 30
python3 scripts/stock.py technical MSFT --period 1y
python3 scripts/stock.py fundamentals AAPL --peers MSFT,GOOGL
python3 scripts/stock.py context TSLA --no-macro
python3 scripts/stock.py research NVDA --period 2y --benchmark '^GSPC' --peers AMD,AVGO
python3 scripts/stock.py backtest NVDA --period 2y --strategy sma --fast 20 --slow 50 --cost-bps 10
python3 scripts/stock.py history NVDA --period 2y > history.json
python3 scripts/stock.py technical NVDA --history-file history.json
python3 scripts/stock.py backtest NVDA --history-file history.json
```

```bash
printf '%s\n' '{"action":"quote","symbol":"NASDAQ:NVDA","providers":["yahoo","tencent"]}' | python3 scripts/stock.py --json-input -
python3 scripts/stock.py --json-input request.json --pretty
```

同样支持 PowerShell 的 `Get-Content -Raw request.json | py -3 scripts/stock.py --json-input -`。JSON 字段以 `snake_case` 表达，例如 `max_bars`、`cost_bps` 和 `no_news`。布尔选项用 JSON 布尔值。 `peers` 和 `providers` 可用数组或逗号分隔字符串。

## 参数

- 股票代码可用 `NVDA`、`BRK-B`、`0700.HK`、`600519.SS`、`000001.SZ`、`7203.T`、`SHEL.L` 等。常见交易所前缀会归一化。纯数字必须加市场后缀。双重上市、ADR 与股类须先核实身份和代码。
- 行情时间范围为 `1d`、`5d`、`1mo`、`3mo`、`6mo`、`1y`、`2y`、`5y`、`10y`、`ytd`、`max`。周期有日、周、月，以及部分源支持的 `1m`、`5m`、`15m`、`30m`、`60m` 和 `1h`。分钟线的回溯长度受供应商限制。
- `--as-of YYYY-MM-DD` 按行情交易市场日期截断历史。财报按已提交日期限制，宏观是当前发布系列按观测日截断，历史新闻目录可能不完整。当前数据、历史分红拆股调整以及宏观修订没有统一的历史数据版本保证。
- 综合研究默认按代码市场选择常见基准，可用 `--benchmark` 指定指数或 ETF，或设为 `none`。最多四只显式同业，由宿主选择、说明可比性。使用 `--no-yahoo` 可跳过 yfinance 基本面，这与行情供应商选择独立。 `--no-news`、`--no-macro` 可减小综合请求范围。
- `history` 默认返回供应商提供的完整窗口，可以用 `--max-bars` 限制输出尾部。 `research` 默认保留行情摘要，经 `--include-bars` 可输出 K 线，`--max-bars` 可同时限制行数。裁剪仅影响输出及后续从本地文件重算的范围。用 `--history-file` 可读取已保存的同标的行情包，日线回测也支持离线运行。
- `backtest` 支持 `sma` 和 `ema`，短期窗口 2 至 299、长期窗口不超过 300，短窗口须小于长窗口。成本为每边 0 至 500 个基点。复权未知时默认拒绝回测，只有在用户确认数据限制后才使用 `--allow-unverified-adjustment`。

## JSON 与单位

顶层携带 `schema_version`、`action`、`symbol`、`generated_at`、`status` 与 `data`。退出码 0 表示 `ok` 或 `partial`，2 表示请求/执行参数错误，3 表示 `unavailable`。使用非法命令行参数时 argparse 用标准错误打印语法。可用性跟随请求的主要数据分支，仍需检查嵌套错误、日期和字段。空字段为 `null` 或省略，允许在主源失败后继承成功备用源的数据。

- 报价的 `time_kind` 区分 `provider_reported_realtime`、`provider_delayed`、`freshness_unverified` 与 `historical_close`。提供者未声明时延时应保留未知，且久未更新的报价应按时间戳解释。
- 行情的 `source` 含提供方、URL、抓取时刻。时间戳含偏移，日线日期指交易地日历。 `price_unit` 与 `currency` 可能不同，例如伦敦报价的便士。成交额来自提供字段，不用成交量乘现价估算。个别提供方用“手”表示量，核对 `volume_unit`。
- OHLC 的 `adjustment` 标注收益复权、仅拆股、提供方前复权、指数价格或未知。 `raw_close` 是该供应商复权前输入，Yahoo 的该列仍按拆股标准化。 `complete=false` 的 bar 不参与技术指标与回测。Yahoo 路线输出可观测的拆股事件。
- 价格表现、风险和回测含 `_pct` 的字段按百分数表示，基准超额为百分比点。财务 `gross_margin`、`net_margin`、ROE、ROA 及同比增长为小数比率。估值用倍数，报表货币以 `financials.currency` 为准，宏观用每个序列自己的单位。
- 当前报告的供应商倍数和 SEC 口径计算结果具有各自财务期和报价时间。历史估值用 SEC 已披露数据和日线估算，只有行情提供拆股证据、币种相同、股数及财报覆盖充足时才输出采样点。
