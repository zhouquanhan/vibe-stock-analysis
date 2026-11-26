# vibe-stock-analysis

**English** · [简体中文](README.md)

Research individual stocks in the agent you already use. `vibe-stock-analysis` is a portable Agent Skill for OpenAI Codex, Google Antigravity, and other Agent Skills clients. Ask “Is NVIDIA’s valuation reasonable?” or “What do Microsoft’s technical indicators show?” Your agent calls Python tools for market data, filings, and calculations, then uses its selected model to write the analysis. [Agent Skills specification](https://agentskills.io/specification)

Install the Skill without deploying Vibe-Trading or configuring a separate model API key. The Python tools make no model API calls, so there is no separate model API bill for the Skill. Your host uses its existing model allowance or billing arrangement.

## What you can research

| Task | Available evidence |
| --- | --- |
| Quotes and technical analysis | Latest available quotes, historical OHLCV, moving averages, MACD, RSI, KDJ, Bollinger Bands, ATR, volatility, drawdown, and benchmark comparisons |
| Financial statements and valuation | Financial statements, SEC filings, PE/PB/PS, peer comparisons, and historical valuation estimates where data supports them |
| Events and backtests | Company news, US macro data, and single-stock SMA/EMA backtests with trading costs and a buy-and-hold baseline |

The CLI returns JSON with data sources, timestamps, currency, and missing fields. It tries fallback quote providers when a source fails. Market coverage and quote freshness vary by provider. [NVDA research example (Chinese)](vibe-stock-analysis/examples/NVDA-2026-11-25.md)

## Quick start

1. Install Python 3.10 or later. Download `vibe-stock-analysis.zip` from the Assets section. [Latest release](https://github.com/zhouquanhan/vibe-stock-analysis/releases/latest)
2. Extract the ZIP and copy the whole `vibe-stock-analysis/` folder into the location for your host. Check that `SKILL.md` is directly inside that folder.
3. Open a new local session in the host and ask “Get the latest available NVDA quote and show its timestamp and source.” Live data requires outbound HTTPS access to the providers.

| Install scope | Skill folder |
| --- | --- |
| Current project, shared by Codex and Antigravity | `<project>/.agents/skills/vibe-stock-analysis/` |
| All local Codex projects | `~/.agents/skills/vibe-stock-analysis/` |
| All local Antigravity projects | `~/.gemini/config/skills/vibe-stock-analysis/` |

To check a project installation from the project root, run the command below. In Windows PowerShell, replace `python3` with `py -3`.

```bash
python3 .agents/skills/vibe-stock-analysis/scripts/stock.py quote NVDA --pretty
```

For more detailed research, ask “Compare NVDA with AMD on earnings, valuation, and technicals. Cite the sources and mark any missing data.”

Automatic Skill discovery and execution were tested on macOS with Codex CLI and Antigravity Desktop. The Chinese guides cover optional Python dependencies and other client layouts. [Skill entry](vibe-stock-analysis/SKILL.md) · [Installation guide](vibe-stock-analysis/README.md) · [CLI reference](vibe-stock-analysis/references/cli.md) · [Test report](vibe-stock-analysis/references/testing.md)

## Credits and license

Thanks to HKUDS and the Vibe-Trading contributors for open-sourcing the data integrations, calculation logic, and research workflows that this Skill adapts. This project uses the MIT license and supports financial research and historical simulation. [Vibe-Trading](https://github.com/HKUDS/Vibe-Trading) · [Upstream reuse](vibe-stock-analysis/references/upstream.md) · [NOTICE](vibe-stock-analysis/NOTICE) · [MIT license](LICENSE)
