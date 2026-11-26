#!/usr/bin/env python3
"""Portable single-stock evidence CLI. Run with --help for invocation options."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
import json
from pathlib import Path
import re
import sys

from analytics import backtest, benchmark_comparison, completed, finite, technical

ACTIONS = ("quote", "history", "technical", "fundamentals", "context", "research", "backtest")
DEFAULTS = {"period": "1y", "interval": "1d", "strategy": "sma", "fast": 20,
            "slow": 50, "cost_bps": 10, "max_bars": 0}
OPTIONAL = {"providers", "as_of", "benchmark", "peers", "history_file",
            "allow_unverified_adjustment", "no_yahoo", "no_news", "no_macro", "include_bars"}
ALLOWED = {
    "quote": {"providers", "as_of"},
    "history": {"period", "interval", "providers", "as_of", "max_bars"},
    "technical": {"period", "interval", "providers", "as_of", "history_file"},
    "fundamentals": {"peers", "as_of", "no_yahoo"},
    "context": {"as_of", "no_news", "no_macro"},
    "research": {"period", "providers", "as_of", "benchmark", "peers", "include_bars",
                 "no_yahoo", "no_news", "no_macro", "max_bars"},
    "backtest": {"period", "providers", "as_of", "history_file", "strategy", "fast",
                 "slow", "cost_bps", "allow_unverified_adjustment"},
}
PERIODS = ("1d", "5d", "1mo", "3mo", "6mo", "1y", "2y", "5y", "10y", "ytd", "max")
INTERVALS = ("1d", "1wk", "1mo", "1m", "5m", "15m", "30m", "60m", "1h")
SYMBOL_MAX_LENGTH = 40


def cli_parser():
    parser = argparse.ArgumentParser(
        description="Read financial evidence and simulate a single-stock strategy. Output is JSON.",
        epilog="Use --json-input - for a JSON request from stdin (or supply a JSON file path).",
    )
    parser.add_argument("--json-input", metavar="FILE")
    parser.add_argument("--pretty", action="store_true")
    commands = parser.add_subparsers(dest="action", metavar="ACTION")
    for action in ACTIONS:
        command = commands.add_parser(action)
        command.add_argument("symbol")
        command.add_argument("--pretty", action="store_true", default=argparse.SUPPRESS)
        for option in sorted(ALLOWED[action]):
            flag = "--" + option.replace("_", "-")
            args = {"default": argparse.SUPPRESS}
            if option in {"fast", "slow", "max_bars"}:
                args["type"] = int
            if option == "cost_bps":
                args["type"] = float
            if option in {"allow_unverified_adjustment", "no_yahoo", "no_news", "no_macro", "include_bars"}:
                args["action"] = "store_true"
            choices = {"period": PERIODS, "interval": INTERVALS, "strategy": ("sma", "ema")}
            if option in choices:
                args["choices"] = choices[option]
            command.add_argument(flag, **args)
    return parser


def _strings(value, name, limit):
    if isinstance(value, str):
        value = value.split(",")
    if not isinstance(value, list) or len(value) > limit:
        raise ValueError(f"{name} must contain at most {limit} entries")
    if not all(isinstance(item, str) and item.strip() for item in value):
        raise ValueError(f"{name} entries must be nonempty strings")
    return list(dict.fromkeys(item.strip() for item in value))


def _validate(request):
    from market import normalize_symbol

    if not isinstance(request, dict):
        raise ValueError("request must be a JSON object")
    action = request.get("action")
    if action not in ACTIONS:
        raise ValueError(f"action must be one of: {', '.join(ACTIONS)}")
    unknown = set(request) - {"action", "symbol"} - ALLOWED[action]
    if unknown:
        raise ValueError("unsupported fields for this action: " + ", ".join(sorted(unknown)))
    options = {**DEFAULTS, **dict.fromkeys(OPTIONAL), **request}
    raw_symbol = options.get("symbol")
    if not isinstance(raw_symbol, str) or not 0 < len(raw_symbol.strip()) <= SYMBOL_MAX_LENGTH:
        raise ValueError("symbol must be a nonempty exchange ticker")
    options["symbol"] = normalize_symbol(raw_symbol)
    for key, choices in (("period", PERIODS), ("interval", INTERVALS), ("strategy", ("sma", "ema"))):
        if options[key] not in choices:
            raise ValueError(f"unsupported {key}")
    for key in ("fast", "slow", "max_bars"):
        if type(options[key]) is not int:
            raise ValueError(f"{key} must be an integer")
    if not 0 <= options["max_bars"] <= 100_000:
        raise ValueError("max_bars must be between 0 and 100000")
    if not finite(options["cost_bps"]):
        raise ValueError("cost_bps must be numeric")
    for key in ("allow_unverified_adjustment", "no_yahoo", "no_news", "no_macro", "include_bars"):
        if key in request and not isinstance(options[key], bool):
            raise ValueError(f"{key} must be boolean")
    for key in ("as_of", "benchmark", "history_file"):
        if key in request and (not isinstance(options[key], str) or not options[key].strip()):
            raise ValueError(f"{key} must be a nonempty string")
    if options["as_of"]:
        if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", options["as_of"]):
            raise ValueError("as_of must be YYYY-MM-DD")
        date.fromisoformat(options["as_of"])
    if options["benchmark"] and options["benchmark"] != "none":
        options["benchmark"] = normalize_symbol(options["benchmark"])
    if options["peers"] is not None:
        options["peers"] = [normalize_symbol(item) for item in _strings(options["peers"], "peers", 4)]
        options["peers"] = list(dict.fromkeys(item for item in options["peers"] if item != options["symbol"]))
    if options["providers"] is not None:
        options["providers"] = _strings(options["providers"], "providers", 8)
        if not options["providers"]:
            raise ValueError("providers must contain at least one entry")
    return options


def _market(options, symbol=None):
    from market import history, market_data

    fetch = market_data if options["action"] == "research" else history
    return fetch(symbol or options["symbol"], period=options["period"], interval=options["interval"],
                 providers=options["providers"], as_of=options["as_of"])


def _history_file(options):
    path = Path(options["history_file"])
    if path.stat().st_size > 20_000_000:
        raise ValueError("history_file exceeds 20 MB")
    exported = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(exported, dict) or exported.get("symbol") != options["symbol"]:
        raise ValueError("history_file symbol must match the requested symbol")
    history = exported.get("history")
    if not isinstance(history, dict) and isinstance(exported.get("data"), dict):
        history = exported["data"].get("history")
    if not isinstance(history, dict) or history.get("interval") not in INTERVALS:
        raise ValueError("history_file must be an exported history response")
    bars = history.get("bars")
    if not isinstance(bars, list) or not all(isinstance(row, dict) for row in bars):
        raise ValueError("history_file bars must be a list of objects")
    if history["interval"] != options["interval"]:
        raise ValueError("history_file interval differs from requested interval")
    cutoff = options["as_of"]
    for bar in bars:
        label = bar.get("date")
        if not isinstance(label, str):
            raise ValueError("every offline bar must carry a date")
        date.fromisoformat(label[:10])
    history = dict(history, bars=[row for row in bars if not cutoff or row["date"][:10] <= cutoff])
    history["source"] = {"provider": "local_history_snapshot", "original_source": history.get("source")}
    return {"symbol": options["symbol"], "status": "ok" if history["bars"] else "unavailable",
            "history": history, "errors": []}


def _compact_market(result, include_bars, limit=0):
    history = result.get("history")
    if not history:
        return result
    bars = history.get("bars", [])
    summary = {key: value for key, value in history.items() if key != "bars"}
    summary.update({"bar_count": len(bars), "completed_bar_count": len(completed(history)),
                    "first_bar": bars[0] if bars else None, "last_bar": bars[-1] if bars else None})
    if include_bars:
        summary["bars"] = bars[-limit:] if limit else bars
        summary["omitted_bar_count"] = len(bars) - len(summary["bars"])
    return dict(result, history=summary)


def _default_benchmark(symbol):
    for suffix, index in ((".SH", "000001.SS"), (".SS", "000001.SS"), (".SZ", "399001.SZ"),
                          (".HK", "^HSI"), (".TO", "^GSPTSE"), (".L", "^FTSE"), (".T", "^N225"),
                          (".DE", "^GDAXI"), (".PA", "^FCHI"), (".AX", "^AXJO"), (".KS", "^KS11"),
                          (".TW", "^TWII"), (".NS", "^NSEI"), (".BO", "^BSESN"),
                          (".SA", "^BVSP")):
        if symbol.endswith(suffix):
            return index
    return "^GSPC" if re.fullmatch(r"[A-Z][A-Z0-9]*(?:-[A-Z])?", symbol) else None


def _fundamentals(options, observed_quote=None):
    from fundamentals import compare_peers, fundamentals

    settings = {"yahoo": not options["no_yahoo"], "as_of": options["as_of"]}
    observed_quote = observed_quote or {}
    compatible_unit = observed_quote.get("price_unit") == observed_quote.get("currency")
    pricing = {"market_currency": observed_quote.get("currency"),
               "market_price": observed_quote.get("price") if compatible_unit else None,
               "price_as_of": observed_quote.get("as_of")}
    result = {"fundamentals": fundamentals(options["symbol"], **settings, **pricing)}
    if options["peers"]:
        result["peers"] = compare_peers(options["peers"], **settings)
    return result


def _context(options):
    from context import context

    return context(options["symbol"], news=not options["no_news"],
                   macro=not options["no_macro"], as_of=options["as_of"])


def _research(options):
    from valuation import historical_valuation

    benchmark = options["benchmark"] or _default_benchmark(options["symbol"])
    with ThreadPoolExecutor(max_workers=4) as pool:
        market_task = pool.submit(_market, options)
        context_task = pool.submit(_context, options)
        benchmark_task = None
        if benchmark and benchmark not in {"none", options["symbol"]}:
            benchmark_task = pool.submit(_market, options, benchmark)
        market = market_task.result()
        funds_task = pool.submit(_fundamentals, options, market.get("quote"))
        sections = {**funds_task.result(), "context": context_task.result()}
        history = market.get("history")
        benchmark_market = benchmark_task.result() if benchmark_task else None

    benchmark_history = (benchmark_market or {}).get("history")
    comparison = benchmark_comparison(history, benchmark_history, benchmark) if history and benchmark_history else None
    result = {"market": _compact_market(market, options["include_bars"], options["max_bars"]),
              "technical": technical(history), **sections, "benchmark": comparison}
    fiscal_data = sections["fundamentals"]["data"]["financials"]
    result["historical_valuation"] = historical_valuation(fiscal_data, history, requested_as_of=options["as_of"])
    if benchmark_task:
        result["benchmark_diagnostics"] = _compact_market(benchmark_market, False)
    available = [market["status"], sections["fundamentals"]["status"], sections["context"]["status"]]
    if benchmark_task:
        available.append("ok" if comparison else "unavailable")
    status = "ok" if all(item == "ok" for item in available) else "partial"
    if all(item == "unavailable" for item in available):
        status = "unavailable"
    return status, result


def execute(request):
    from market import quote

    options = _validate(request)
    action, symbol = options["action"], options["symbol"]
    if action == "research":
        status, data = _research(options)
    elif action == "fundamentals":
        market_quote = quote(symbol, as_of=options["as_of"])
        data = _fundamentals(options, market_quote.get("quote"))
        data["valuation_quote"] = market_quote
        status = data["fundamentals"]["status"]
    elif action == "context":
        data = {"context": _context(options)}
        status = data["context"]["status"]
    elif action == "quote":
        data = quote(symbol, providers=options["providers"], as_of=options["as_of"])
        status = data["status"]
    else:
        data = _history_file(options) if options["history_file"] else _market(options)
        history = data.get("history")
        status = "ok" if history else "unavailable"
        if action == "history":
            data = _compact_market(data, True, options["max_bars"])
        else:
            data = _compact_market(data, False)
            if action == "technical":
                data["technical"] = technical(history)
                status = "ok" if data["technical"] else "unavailable"
            elif history:
                data["backtest"] = backtest(history, options["fast"], options["slow"], options["cost_bps"],
                                            options["strategy"], options["allow_unverified_adjustment"])
            else:
                data["backtest"] = None
    return {"schema_version": "1.0", "action": action, "symbol": symbol,
            "requested_as_of": options["as_of"], "generated_at": datetime.now(timezone.utc).isoformat(),
            "status": status, "data": data}


def main():
    parser = cli_parser()
    args = vars(parser.parse_args())
    pretty = args.pop("pretty", False)
    json_input = args.pop("json_input")
    try:
        if json_input:
            if args["action"] is not None:
                raise ValueError("choose JSON input or an action subcommand")
            raw = sys.stdin.read(1_000_001) if json_input == "-" else Path(json_input).read_text(encoding="utf-8")
            if len(raw) > 1_000_000:
                raise ValueError("JSON request exceeds maximum length")
            args = json.loads(raw)
        result = execute(args)
        output = json.dumps(result, ensure_ascii=True, allow_nan=False, indent=2 if pretty else None)
    except (ValueError, OSError, OverflowError) as exc:
        output = json.dumps({"schema_version": "1.0", "status": "error",
                             "error": {"type": type(exc).__name__, "message": str(exc)}})
        print(output)
        return 2
    print(output)
    return 0 if result["status"] in {"ok", "partial"} else 3


if __name__ == "__main__":
    raise SystemExit(main())
