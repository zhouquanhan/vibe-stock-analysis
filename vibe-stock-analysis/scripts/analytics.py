"""Deterministic price analytics and a single-stock strategy simulation.

SMA/EMA/MACD/Bollinger, volume and fingerprinting adapted from Vibe-Trading
agent/src/tools/technical_indicator_tool.py. Drawdown conventions adapted from
agent/backtest/metrics.py. Copyright (c) 2026 Vibe-Trading Contributors (MIT).
"""
from __future__ import annotations

import hashlib
import json
import math
from datetime import date, timedelta
from statistics import mean, stdev


def finite(value):
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value))


def completed(history):
    """Canonicalize already validated provider bars for deterministic analytics."""
    rows = {}
    for bar in (history or {}).get("bars", []):
        if bar.get("complete") is not True:
            continue
        label = bar.get("date")
        if isinstance(label, str) and finite(bar.get("close")) and bar["close"] > 0:
            rows[label] = bar
    return [rows[key] for key in sorted(rows)]


def prices(history, bars):
    # The provider layer already normalizes the whole OHLC series to one basis.
    points = []
    for bar in bars:
        values = {key: (bar[key] if finite(bar.get(key)) and bar[key] > 0 else None)
                  for key in ("open", "high", "low")}
        points.append(dict(values, close=bar["close"], date=bar["date"], volume=bar.get("volume")))
    return history.get("adjustment", "unknown"), points

def sma(values, window):
    return mean(values[-window:]) if len(values) >= window else None


def ema_series(values, period):
    if not values:
        return []
    alpha = 2 / (period + 1)
    result = [values[0]]
    for value in values[1:]:
        result.append(alpha * value + (1 - alpha) * result[-1])
    return result


def wilder(values, period):
    if len(values) < period:
        return None
    result = mean(values[:period])
    for value in values[period:]:
        result += (value - result) / period
    return result


def rsi(values, period=14):
    deltas = [end - start for start, end in zip(values, values[1:])]
    gains = wilder([max(0, delta) for delta in deltas], period)
    losses = wilder([max(0, -delta) for delta in deltas], period)
    if losses is None:
        return None
    if losses == 0:
        return 50.0 if gains == 0 else 100.0
    return 100 - 100 / (1 + gains / losses)


def macd(values):
    if len(values) < 35:
        return None
    lines = [a - b for a, b in zip(ema_series(values, 12), ema_series(values, 26))]
    signal = ema_series(lines, 9)
    histogram = lines[-1] - signal[-1]
    previous = lines[-2] - signal[-2]
    return {"line": lines[-1], "signal": signal[-1], "histogram": histogram,
            "crossover": direction_change(previous, histogram)}


def direction_change(previous, current):
    if previous <= 0 < current:
        return "up"
    if previous >= 0 > current:
        return "down"
    return None


def band(values):
    mid = sma(values, 20)
    if mid is None:
        return None
    spread = 2 * stdev(values[-20:])
    return {"lower": mid - spread, "middle": mid, "upper": mid + spread}


def kdj(points, period=9):
    if len(points) < period:
        return None
    k = d = 50.0
    for index in range(period, len(points) + 1):
        window = points[index - period:index]
        high = max(point["high"] for point in window)
        low = min(point["low"] for point in window)
        rsv = (100 * (window[-1]["close"] - low) / (high - low)) if high > low else 50
        k = (2 * k + rsv) / 3
        d = (2 * d + k) / 3
    return {"k": k, "d": d, "j": 3 * k - 2 * d}


def atr(points, period=14):
    if not points:
        return None
    ranges = [points[0]["high"] - points[0]["low"]]
    for before, current in zip(points, points[1:]):
        high, low, close = current["high"], current["low"], before["close"]
        ranges.append(max(high - low, abs(high - close), abs(low - close)))
    return wilder(ranges, period)


def max_drawdown(values, initial=None):
    if not values:
        return None
    peak = initial if initial is not None else values[0]
    worst = 0.0
    for value in values:
        peak = max(peak, value)
        worst = min(worst, value / peak - 1)
    return worst


def period_returns(points, end_label=None, day_windows=(30, 90, 180, 365)):
    """Use the last close on/before each calendar target, within seven days."""
    if not points:
        return {}
    end = date.fromisoformat((end_label or points[-1]["date"])[:10])
    by_day = {date.fromisoformat(point["date"][:10]): point for point in points}
    available = sorted(day for day in by_day if day <= end)
    if not available:
        return {}
    last = available[-1]
    answer = {}
    for days in day_windows:
        target = end - timedelta(days=days)
        eligible = [d for d in available if target - timedelta(days=7) <= d <= target]
        if not eligible or (end - last).days > 7:
            continue
        first = eligible[-1]
        answer[f"{days}d"] = {
            "start": first.isoformat(), "end": last.isoformat(),
            "return_pct": 100 * (by_day[last]["close"] / by_day[first]["close"] - 1),
        }
    return answer


def technical(history):
    bars = completed(history)
    if not bars:
        return None
    basis, points = prices(history, bars)
    close = [point["close"] for point in points]
    # A missing high/low limits OHLC-based indicators to the latest contiguous suffix.
    ohlc = []
    for point in reversed(points):
        if point["high"] is None or point["low"] is None:
            break
        ohlc.append(point)
    ohlc.reverse()

    volume = [point["volume"] for point in points[-20:]]
    volume_avg = mean(volume) if len(volume) == 20 and all(finite(v) for v in volume) else None
    moving = {str(n): sma(close, n) for n in (5, 20, 50, 200)}
    exponential = {str(n): ema_series(close, n)[-1] if len(close) >= n else None
                   for n in (12, 20, 26, 50, 200)}
    trend = {name: ("above_ma" if close[-1] > moving[str(n)] else
                    "below_ma" if close[-1] < moving[str(n)] else "at_ma")
             for name, n in (("short", 20), ("medium", 50), ("long", 200))
             if moving[str(n)] is not None}
    levels = None
    if len(ohlc) >= 20:
        window = ohlc[-20:]
        levels = {"support": min(point["low"] for point in window),
                  "resistance": max(point["high"] for point in window), "lookback_bars": 20}
        if len(ohlc) >= 21:
            prior_window = ohlc[-21:-1]
            if close[-1] > max(point["high"] for point in prior_window):
                levels["breakout_against_prior_window"] = "above"
            elif close[-1] < min(point["low"] for point in prior_window):
                levels["breakout_against_prior_window"] = "below"
    returns = [math.log(b / a) for a, b in zip(close, close[1:])]
    frequency = {"1d": 252, "1wk": 52, "1mo": 12}.get(history.get("interval"))
    return {
        "as_of": bars[-1]["date"], "sample_start": bars[0]["date"], "bars": len(bars),
        "currency": history.get("currency"), "interval": history.get("interval"),
        "source": history.get("source"), "price_basis": basis,
        "data_sha256": hashlib.sha256(json.dumps(points, separators=(",", ":"),
                                                 sort_keys=True).encode()).hexdigest(),
        "close": close[-1], "ma": moving, "ema": exponential, "macd": macd(close),
        "rsi_14": rsi(close), "kdj_9_3_3": kdj(ohlc), "bollinger_20_2": band(close),
        "atr_14": atr(ohlc), "rolling_extremes": levels, "trend_vs_ma": trend,
        "volume": {"latest": volume[-1], "ma_20": volume_avg,
                   "ratio_20": volume[-1] / volume_avg if volume_avg else None,
                   "unit": history.get("volume_unit")},
        "performance": {
            "sample_return_pct": 100 * (close[-1] / close[0] - 1),
            "calendar_windows": period_returns(points) if frequency == 252 else {},
            "annualized_log_volatility_pct": (100 * stdev(returns) * math.sqrt(frequency)
                                              if len(returns) >= 2 and frequency else None),
            "max_drawdown_pct": 100 * max_drawdown(close),
            "bars_per_year": frequency,
        },
    }


def benchmark_comparison(stock, benchmark, symbol):
    stock_basis, stock_points = prices(stock, completed(stock))
    bench_basis, bench_points = prices(benchmark, completed(benchmark))
    if not stock_points or not bench_points:
        return None
    a = {point["date"]: point for point in stock_points}
    b = {point["date"]: point for point in bench_points}
    common = sorted(a.keys() & b.keys())
    if len(common) < 2:
        return None
    first, last = common[0], common[-1]
    returns_a = 100 * (a[last]["close"] / a[first]["close"] - 1)
    returns_b = 100 * (b[last]["close"] / b[first]["close"] - 1)
    windows_a = period_returns([a[key] for key in common], last)
    windows_b = period_returns([b[key] for key in common], last)
    windows = {key: {"start": windows_a[key]["start"], "end": last,
                     "stock_pct": v["return_pct"], "benchmark_pct": windows_b[key]["return_pct"],
                     "excess_pp": v["return_pct"] - windows_b[key]["return_pct"]}
               for key, v in windows_a.items()}
    return {"symbol": symbol, "start": first, "end": last, "common_bars": len(common),
            "stock_pct": returns_a, "benchmark_pct": returns_b, "excess_pp": returns_a - returns_b,
            "windows": windows, "stock_basis": stock_basis, "benchmark_basis": bench_basis,
            "stock_currency": stock.get("currency"), "benchmark_currency": benchmark.get("currency"),
            "source": benchmark.get("source"), "method": "overlapping dates; local-currency series"}


def backtest(history, fast, slow, cost_bps, strategy="sma", allow_unverified=False):
    if history.get("interval") != "1d":
        raise ValueError("backtest requires daily bars")
    if not 2 <= fast < slow <= 300:
        raise ValueError("require 2 <= fast < slow <= 300")
    if not finite(cost_bps) or not 0 <= cost_bps <= 500:
        raise ValueError("cost_bps must be between 0 and 500")
    basis, bars = prices(history, completed(history))
    if len(bars) < slow + 1:
        raise ValueError(f"backtest needs at least {slow + 1} completed daily bars")
    if basis not in {"split_dividend", "split_only", "provider_forward"} and not allow_unverified:
        raise ValueError("corporate-action adjustment unverified; change provider or opt in explicitly")
    if any(bar["open"] is None for bar in bars[slow:]):
        raise ValueError("completed execution dates contain missing open prices")
    closes = [bar["close"] for bar in bars]
    if strategy == "ema":
        fast_ma = ema_series(closes, fast)
        slow_ma = ema_series(closes, slow)
    else:
        fast_ma = [sma(closes[max(0, n + 1 - fast):n + 1], fast) for n in range(len(closes))]
        slow_ma = [sma(closes[max(0, n + 1 - slow):n + 1], slow) for n in range(len(closes))]
    fee = cost_bps / 10_000
    cash, units, paid, entry_equity = 1.0, 0.0, 0.0, None
    reference_entry = bars[slow]["open"]
    reference_units = 1 / (reference_entry * (1 + fee))
    orders, rounds, path = [], [], []
    for i in range(slow, len(bars)):
        bar = bars[i]
        target_long = fast_ma[i - 1] > slow_ma[i - 1]
        if target_long and units == 0:
            entry_equity = cash
            units = cash / (bar["open"] * (1 + fee))
            cost = units * bar["open"] * fee
            cash = 0.0
            paid += cost
            orders.append({"signal_date": bars[i - 1]["date"], "execution_date": bar["date"],
                           "side": "buy", "price_in_price_basis": bar["open"], "cost": cost})
        elif not target_long and units > 0:
            cost = units * bar["open"] * fee
            cash = units * bar["open"] - cost
            rounds.append(cash / entry_equity - 1)
            units = 0.0
            paid += cost
            orders.append({"signal_date": bars[i - 1]["date"], "execution_date": bar["date"],
                           "side": "sell", "price_in_price_basis": bar["open"], "cost": cost})
        path.append({"date": bar["date"], "equity": cash + units * bar["close"],
                     "buy_and_hold": reference_units * bar["close"], "long": units > 0})

    equity = [row["equity"] for row in path]
    reference = [row["buy_and_hold"] for row in path]
    changes = [b / a - 1 for a, b in zip([1.0, *equity], equity)]
    volatility = stdev(changes) if len(changes) > 1 else None
    return {
        "strategy": f"{strategy}_cross_long_cash", "fast": fast, "slow": slow,
        "cost_bps_per_side": cost_bps, "price_basis": basis, "source": history.get("source"),
        "warmup_start": bars[0]["date"], "start": path[0]["date"], "end": path[-1]["date"],
        "bars": len(path), "total_return_pct": 100 * (equity[-1] - 1),
        "buy_and_hold_pct": 100 * (reference[-1] - 1),
        "max_drawdown_pct": 100 * max_drawdown(equity, 1.0),
        "buy_and_hold_max_drawdown_pct": 100 * max_drawdown(reference, 1.0),
        "annualized_volatility_pct": (100 * volatility * math.sqrt(252)
                                     if volatility is not None else None),
        "sharpe_zero_risk_free": (mean(changes) / volatility * math.sqrt(252) if volatility else None),
        "exposure_pct": 100 * sum(row["long"] for row in path) / len(path),
        "closed_round_trips": len(rounds),
        "closed_round_trip_win_pct": 100 * sum(x > 0 for x in rounds) / len(rounds) if rounds else None,
        "paid_cost_in_initial_equity_units": paid, "open_position": units > 0,
        "orders": orders, "equity_curve": path,
        "assumptions": ["close signal, next observed open execution", "fractional shares; gross long or cash",
                        "observed dates only", "open position marked to market on final close",
                        "no taxes, cash interest or liquidity model; one-way cost includes spread/slippage proxy",
                        "adjusted OHLC is a synthetic return series where used"],
    }
