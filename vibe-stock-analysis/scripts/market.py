"""Read-only quote and bar retrieval, with source-specific provenance.

Yahoo chart parsing, Vibe/Yahoo ticker mapping, Tencent response/request
format, and Sina JSONP extraction adapted from HKUDS/Vibe-Trading
(agent/backtest/loaders/{yahoo_client,tencent_loader,sina_loader}.py).
Copyright (c) 2026 Vibe-Trading Contributors. See ../LICENSE.
"""

from __future__ import annotations

import csv
import io
import json
import math
import os
import re
import shutil
import ssl
import subprocess
import time
from collections.abc import Sequence
from datetime import date, datetime, time as clock_time, timedelta, timezone
from email.utils import parsedate_to_datetime
from urllib.error import HTTPError, URLError
from urllib.parse import quote as encode_path
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

PRICE_FIELDS = ("open", "high", "low", "close")
INTERVALS = {"1m", "5m", "15m", "30m", "60m", "1h", "1d", "1wk", "1mo"}
PERIOD_DAYS = {"1d": 1, "5d": 7, "1mo": 31, "3mo": 92, "6mo": 183,
               "1y": 365, "2y": 731, "5y": 1827, "10y": 3653, "max": 73049}
PERIODS = set(PERIOD_DAYS) | {"ytd"}
YAHOO = "https://query1.finance.yahoo.com/v8/finance/chart"
TENCENT_HISTORY = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
TENCENT_QUOTE = "https://qt.gtimg.cn/q="
SINA = ("https://stock.finance.sina.com.cn/usstock/api/jsonp_v2.php/"
        "var%20x=/US_MinKService.getDailyK")
STOOQ = "https://stooq.com/q/d/l/"
USER_AGENT = "Mozilla/5.0 (compatible; stock-research-cli/1.0)"
TIMEOUT = 6
_DEFAULT_PROVIDERS = ("yahoo", "yfinance", "tencent", "sina", "stooq")


class ProviderError(Exception):
    """A provider cannot furnish data for this request."""


def normalize_symbol(symbol: str) -> str:
    """Normalize an exchange-qualified ticker to Yahoo's canonical convention."""
    if not isinstance(symbol, str):
        raise ValueError("symbol must be a string")
    ticker = symbol.strip().upper()
    venue, sep, base = ticker.partition(":")
    if sep:
        suffix = {
            "NASDAQ": "", "NYSE": "", "AMEX": "", "US": "",
            "HKEX": ".HK", "HK": ".HK", "SSE": ".SS", "SH": ".SS",
            "SZSE": ".SZ", "SZ": ".SZ", "TSE": ".T", "NSE": ".NS",
            "BSE": ".BO", "LSE": ".L", "TSX": ".TO",
        }.get(venue)
        if suffix is None:
            raise ValueError(f"unsupported exchange prefix: {venue}")
        ticker = base if suffix and base.endswith(suffix) else base + suffix
    if ticker.endswith(".US"):
        ticker = ticker[:-3].replace(".", "-")
    if re.fullmatch(r"[A-Z]{1,5}\.[AB]", ticker):
        ticker = ticker.replace(".", "-")
    if re.fullmatch(r"\d{6}\.SH", ticker):
        ticker = ticker[:-3] + ".SS"
    if re.fullmatch(r"\d{1,5}\.HK", ticker):
        ticker = f"{int(ticker.split('.')[0]):04}.HK"
    if re.fullmatch(r"\d+", ticker):
        raise ValueError("numeric stock codes require a market suffix, e.g. 0700.HK or 600519.SS")
    if not re.fullmatch(r"\^?[A-Z0-9][A-Z0-9.\-&]{0,22}(?:=[FX])?", ticker):
        raise ValueError("invalid stock ticker or benchmark symbol")
    return ticker


def _number(value):
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _pct(last, previous):
    return (last / previous - 1) * 100 if last is not None and previous else None


def _zone(name):
    try:
        return ZoneInfo(name) if name else None
    except ZoneInfoNotFoundError:
        return None


def _now():
    return datetime.now(timezone.utc)


def _iso(moment):
    return moment.isoformat(timespec="seconds")


def _epoch(value, zone):
    if isinstance(value, datetime):
        return value.astimezone(zone or timezone.utc) if value.tzinfo else None
    try:
        return datetime.fromtimestamp(float(value), tz=zone or timezone.utc)
    except (ValueError, TypeError, OverflowError, OSError):
        return None


def _source(name, url):
    return {"provider": name, "url": url, "retrieved_at": _iso(_now())}


def _system_curl(url, headers):
    # Some macOS Python installs lack the system trust roots, even when
    # /usr/bin/curl can validate the site. Both transports verify TLS.
    executable = "/usr/bin/curl" if os.path.isfile("/usr/bin/curl") else shutil.which("curl")
    if not executable:
        raise ProviderError("TLS trust validation failed; configure Python's trusted CA roots")
    command = [executable, "--silent", "--show-error", "--fail", "--location",
               "--max-time", str(TIMEOUT), "--proto", "=https", "--proto-redir", "=https"]
    for key, value in headers.items():
        command.extend(("--header", f"{key}: {value}"))
    try:
        result = subprocess.run([*command, url], capture_output=True, check=True, timeout=TIMEOUT + 2)
    except (subprocess.SubprocessError, OSError) as exc:
        raise ProviderError("verified system cURL fallback failed after Python TLS error") from exc
    return result.stdout


def _http(url, *, params=None, referer=None):
    if params:
        url += "?" + urlencode(params)
    headers = {"User-Agent": USER_AGENT, "Accept": "*/*"}
    if referer:
        headers["Referer"] = referer
    for attempt in range(2):
        try:
            with urlopen(Request(url, headers=headers), timeout=TIMEOUT) as response:
                return response.read(), url
        except HTTPError as exc:
            header = exc.headers.get("Retry-After") if exc.headers else None
            retry_after = _number(header)
            if header and retry_after is None:
                try:
                    retry_after = (parsedate_to_datetime(header) - _now()).total_seconds()
                except (ValueError, TypeError):
                    pass
            exc.close()
            if attempt or exc.code not in {429, 500, 502, 503, 504} or (retry_after and retry_after > 2):
                raise ProviderError(f"HTTP {exc.code}") from exc
            time.sleep(retry_after if retry_after and retry_after > 0 else 0.5)
        except URLError as exc:
            if isinstance(exc.reason, ssl.SSLCertVerificationError):
                return _system_curl(url, headers), url
            if attempt:
                raise ProviderError(f"network error: {exc.reason}") from exc
            time.sleep(0.4)
        except (TimeoutError, ConnectionError) as exc:
            if attempt:
                raise ProviderError(f"connection failed: {exc}") from exc
            time.sleep(0.4)
    raise ProviderError("request failed")


def _window(as_of, period, zone):
    local_now = _now().astimezone(zone or timezone.utc)
    if as_of is None:
        end = local_now
    elif isinstance(as_of, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", as_of):
        end = datetime.combine(date.fromisoformat(as_of), clock_time.max, tzinfo=zone or timezone.utc)
    else:
        end = datetime.fromisoformat(as_of.replace("Z", "+00:00")) if isinstance(as_of, str) else as_of
        if not isinstance(end, datetime) or end.tzinfo is None:
            raise ValueError("as_of must be YYYY-MM-DD or an ISO timestamp with offset")
        end = end.astimezone(zone or timezone.utc)
    end = min(end, local_now)
    start = date(end.year, 1, 1) if period == "ytd" else end.date() - timedelta(days=PERIOD_DAYS[period])
    return start, end


def _equity_market(ticker):
    if ticker.endswith(".HK"):
        return "HKEX", "HKD", "Asia/Hong_Kong"
    if ticker.endswith(".SS"):
        return "SSE", "CNY", "Asia/Shanghai"
    if ticker.endswith(".SZ"):
        return "SZSE", "CNY", "Asia/Shanghai"
    if re.fullmatch(r"[A-Z]{1,6}(?:-[A-Z])?", ticker):
        return "US", "USD", "America/New_York"
    return None, None, None


def _market_metadata(meta):
    currency = meta.get("currency")
    return {
        "market": meta.get("fullExchangeName") or meta.get("exchangeName"),
        "currency": "GBP" if currency in {"GBp", "GBX"} else currency,
        "price_unit": "GBp" if currency in {"GBp", "GBX"} else currency,
        "timezone": meta.get("exchangeTimezoneName"),
        "company_name": meta.get("longName") or meta.get("shortName"),
        "instrument_type": meta.get("instrumentType"),
    }


def _bar(day, prices, volume, *, adjusted_close=None, turnover=None, complete=True):
    raw = {name: _number(prices[name]) for name in PRICE_FIELDS}
    if any(value is None or value <= 0 for value in raw.values()):
        return None
    if raw["low"] > min(raw["open"], raw["close"]) or raw["high"] < max(raw["open"], raw["close"]):
        return None
    known_adjusted = _number(adjusted_close)
    ratio = known_adjusted / raw["close"] if known_adjusted is not None else 0
    usable_ratio = 0.01 <= ratio <= 100
    adjusted = {name: value * ratio for name, value in raw.items()} if usable_ratio else raw
    observed_volume = _number(volume)
    return {
        "date": day, **adjusted, "raw_close": raw["close"],
        "volume": observed_volume if observed_volume is not None and observed_volume >= 0 else None,
        "turnover": _number(turnover), "complete": complete, "dividend_adjusted": usable_ratio,
    }


def _complete(moment, end, interval, metadata=None):
    if interval not in {"1d", "1wk", "1mo"}:
        minutes = 60 if interval in {"60m", "1h"} else int(interval[:-1])
        return moment + timedelta(minutes=minutes) <= end
    if interval == "1wk":
        return moment.date() - timedelta(days=moment.weekday()) < end.date() - timedelta(days=end.weekday())
    if interval == "1mo":
        return (moment.year, moment.month) < (end.year, end.month)
    if moment.date() != end.date():
        return moment.date() < end.date()
    regular = ((metadata or {}).get("currentTradingPeriod") or {}).get("regular") or {}
    market_close = _epoch(regular.get("end"), end.tzinfo) if isinstance(regular, dict) else None
    if market_close is not None and market_close.date() == end.date():
        return end >= market_close
    if end.time() == clock_time.max:
        return True
    conservative_close = {"America/New_York": clock_time(16, 15), "Asia/Hong_Kong": clock_time(16, 20),
                          "Asia/Shanghai": clock_time(15, 15)}.get(str(end.tzinfo))
    return bool(conservative_close and end.time() >= conservative_close)


def _history(bars, period, interval, adjustment, metadata, source, volume_unit=None):
    observed = sorted({bar["date"]: bar for bar in bars if bar}.values(), key=lambda bar: bar["date"])
    if not observed:
        return None
    return {
        "bars": observed, "period": period, "interval": interval,
        "adjustment": adjustment, "as_of": observed[-1]["date"], "time_kind": "historical_bars",
        "last_completed_bar": next((bar["date"] for bar in reversed(observed) if bar["complete"]), None),
        "volume_unit": volume_unit, "splits": None, **metadata, "source": source,
    }


def _quote(ticker, price, stamp, kind, metadata, source, *, volume=None, change=None, turnover=None,
           previous=None, change_absolute=None, trading_status=None, delay_minutes=None, volume_unit=None):
    price = _number(price)
    if price is None or price <= 0 or not stamp:
        return None
    previous = _number(previous)
    price_delta = _number(change_absolute)
    return {
        "symbol": ticker, "price": price, "as_of": stamp, "time_kind": kind,
        "change_pct": _number(change), "previous_close": previous,
        "change_absolute": price_delta if price_delta is not None else price - previous if previous else None,
        "volume": _number(volume), "volume_unit": volume_unit, "turnover": _number(turnover),
        "turnover_currency": metadata.get("currency") if turnover is not None else None,
        "trading_status": trading_status, "delay_minutes": delay_minutes,
        **metadata, "source": source,
    }


def _completed_close_quote(ticker, history):
    if not history or history["interval"] != "1d":
        return None
    rows = [row for row in history["bars"] if row["complete"]]
    if not rows:
        return None
    last = rows[-1]
    last_price = last["raw_close"]
    previous = rows[-2]["raw_close"] if len(rows) >= 2 else None
    meta = {key: history[key] for key in
            ("market", "currency", "price_unit", "timezone", "company_name", "instrument_type")}
    quote = _quote(ticker, last_price, last["date"], "historical_close", meta, history["source"],
                   volume=last["volume"], change=_pct(last_price, previous), turnover=last["turnover"],
                   previous=previous, trading_status="completed_session", volume_unit=history["volume_unit"])
    if quote:
        quote["price_basis"] = history.get("fallback_close_basis", history["adjustment"])
    return quote


def _at(values, index):
    return values[index] if isinstance(values, list) and index < len(values) else None


def _yahoo_quote(ticker, meta, bars, source, as_of):
    if as_of is not None:
        return None
    metadata = _market_metadata(meta)
    zone = _zone(metadata["timezone"])
    moment = _epoch(meta.get("regularMarketTime"), zone)
    if not moment or moment > _now() + timedelta(minutes=2):
        return None
    price = _number(meta.get("regularMarketPrice"))
    previous = _number(meta.get("regularMarketPreviousClose") or meta.get("previousClose"))
    if previous is None:
        older = [row for row in bars if row and row["date"][:10] < moment.date().isoformat()]
        if older:
            previous = older[-1]["raw_close"]
    change = _number(meta.get("regularMarketChangePercent"))
    previous_is_comparable = not previous or change is None or abs((_pct(price, previous) or 0) - change) <= 0.2
    if not previous_is_comparable:
        previous = None
    delay = _number(meta.get("exchangeDataDelayedBy"))
    state = meta.get("marketState")
    regular = (meta.get("currentTradingPeriod") or {}).get("regular") or {}
    market_close = _epoch(regular.get("end"), zone) if isinstance(regular, dict) else None
    if state in {"CLOSED", "POST", "POSTPOST", "PRE", "PREPRE"}:
        kind = "historical_close"
    elif market_close and moment.date() == market_close.date() and moment >= market_close:
        kind = "historical_close"
    elif moment.date() < _now().astimezone(zone or timezone.utc).date():
        matching_bar = next((bar for bar in bars if bar and bar["date"] == moment.date().isoformat()), None)
        kind = "historical_close" if matching_bar and price and abs(price - matching_bar["raw_close"]) < 0.01 else "historical_observation"
    elif delay is not None and delay > 0:
        kind = "provider_delayed"
    elif delay == 0:
        kind = "provider_reported_realtime"
    else:
        kind = "freshness_unverified"
    volume_unit = "shares" if meta.get("instrumentType") in {"EQUITY", "ETF"} else None
    result = _quote(
        ticker, price, _iso(moment), kind, metadata, source,
        volume=meta.get("regularMarketVolume"), change=change if change is not None else _pct(price, previous),
        previous=previous, trading_status=state, delay_minutes=delay, volume_unit=volume_unit,
    )
    if result:
        result["price_basis"] = "regular_market_price"
    return result


def _yahoo_result(ticker, period, interval, as_of, raw_rows, meta, source, raw_splits):
    metadata = _market_metadata(meta)
    zone = _zone(metadata["timezone"])
    start, end = _window(as_of, period, zone)
    bars = []
    for stamp, values, volume, adj in raw_rows:
        moment = _epoch(stamp, zone)
        coarse = interval in {"1d", "1wk", "1mo"}
        before_window = moment and moment.date() < start and interval not in {"1wk", "1mo"}
        if not moment or before_window or moment > end:
            continue
        bar_date = moment.date().isoformat() if coarse else _iso(moment)
        complete = _complete(moment, end, interval, meta)
        if as_of is not None and not complete:
            # A provider can return the full bar for a session that was still
            # open at the historical cutoff. That bar is future information.
            continue
        bars.append(_bar(bar_date, values, volume, adjusted_close=adj,
                         complete=complete))
    bars = [bar for bar in bars if bar]
    usable = [bar for bar in bars if bar["complete"]] or bars
    all_adjusted = bool(usable) and all(bar["dividend_adjusted"] for bar in usable)
    if not all_adjusted and bars and any(bar["dividend_adjusted"] for bar in bars):
        # Rebuild the whole series at one caliber if any completed bar lacks
        # its adjustment factor. Mixing price bases can fabricate returns.
        for bar in bars:
            ratio = bar["raw_close"] / bar["close"]
            bar.update({key: bar[key] * ratio for key in PRICE_FIELDS})
            bar["dividend_adjusted"] = False
    adjustment = "split_dividend" if all_adjusted else "split_only"
    if adjustment == "split_dividend":
        for bar in bars:
            if not bar["dividend_adjusted"]:
                bar["complete"] = False
    unit = "shares" if metadata["instrument_type"] in {"EQUITY", "ETF"} else None
    if metadata["instrument_type"] == "INDEX":
        adjustment = "provider_index_price"
    history = _history(bars, period, interval, adjustment, metadata, source, unit)
    if history:
        history["fallback_close_basis"] = (adjustment if adjustment == "provider_index_price"
                                            else "split_only")
        splits = []
        if raw_splits is not None:
            for stamp, ratio, numerator, denominator in raw_splits:
                moment = _epoch(stamp, zone)
                if moment and moment.date() >= start and moment <= end and ratio and ratio > 0:
                    splits.append({"date": moment.date().isoformat(), "ratio": ratio,
                                   "numerator": numerator, "denominator": denominator})
        history["splits"] = sorted(splits, key=lambda item: item["date"]) if raw_splits is not None else None
    return _yahoo_quote(ticker, meta, sorted(bars, key=lambda row: row["date"]), source, as_of), history


def _yahoo(ticker, period, interval, as_of, need_quote, need_history):
    url = f"{YAHOO}/{encode_path(ticker, safe='')}"
    params = {"interval": interval, "events": "div,splits", "includePrePost": "false"}
    if as_of is None:
        params["range"] = period
    else:
        zone = _zone(_equity_market(ticker)[2])
        start, end = _window(as_of, period, zone)
        # Request the full earliest possible UTC span and filter in the
        # timezone declared by the exchange response.
        earliest = datetime.combine(start - timedelta(days=1), clock_time(), tzinfo=timezone.utc)
        latest = end + timedelta(days=1)
        params.update(period1=int(earliest.timestamp()), period2=int(latest.timestamp()))
    body, url = _http(url, params=params)
    chart = json.loads(body).get("chart") or {}
    if chart.get("error"):
        error = chart["error"]
        raise ProviderError(f"Yahoo chart: {error.get('code', 'error')} {error.get('description', '')}")
    results = chart.get("result") or []
    if not results:
        raise ProviderError("Yahoo chart returned no result")
    result = results[0]
    indicators = result.get("indicators") or {}
    quotes = (indicators.get("quote") or [{}])[0] or {}
    adjusted = ((indicators.get("adjclose") or [{}])[0] or {}).get("adjclose")
    rows = [
        (stamp, {key: _at(quotes.get(key), index) for key in PRICE_FIELDS},
         _at(quotes.get("volume"), index), _at(adjusted, index))
        for index, stamp in enumerate(result.get("timestamp") or [])
    ]
    split_records = ((result.get("events") or {}).get("splits") or {}).values()
    splits = []
    for entry in split_records:
        numerator = _number(entry.get("numerator"))
        denominator = _number(entry.get("denominator"))
        if (numerator is None or denominator is None) and isinstance(entry.get("splitRatio"), str):
            left, sep, right = entry["splitRatio"].partition(":")
            if sep:
                numerator, denominator = _number(left), _number(right)
        ratio = numerator / denominator if numerator and denominator else None
        splits.append((entry.get("date"), ratio, numerator, denominator))
    return _yahoo_result(ticker, period, interval, as_of, rows, result.get("meta") or {},
                         _source("yahoo", url), splits)


def _yfinance(ticker, period, interval, as_of, need_quote, need_history):
    try:
        import yfinance as yf
    except ImportError as exc:
        raise ProviderError("optional yfinance package is not installed") from exc
    item = yf.Ticker(ticker)
    options = {"interval": interval, "auto_adjust": False, "actions": True, "timeout": TIMEOUT,
               "raise_errors": True}
    if as_of is None:
        options["period"] = period
    else:
        zone = _zone(_equity_market(ticker)[2])
        start, end = _window(as_of, period, zone)
        options.update(start=start.isoformat(), end=(end.date() + timedelta(days=1)).isoformat())
    frame = item.history(**options)
    if frame.empty:
        raise ProviderError("yfinance returned no bars")
    meta = item.get_history_metadata() or {}
    if not meta.get("exchangeTimezoneName") and frame.index.tz is not None:
        meta["exchangeTimezoneName"] = str(frame.index.tz)
    rows = [
        (stamp.timestamp(), {name: row.get(name.capitalize()) for name in PRICE_FIELDS},
         row.get("Volume"), row.get("Adj Close"))
        for stamp, row in frame.iterrows()
    ]
    splits = None
    if "Stock Splits" in frame.columns:
        splits = [(stamp.timestamp(), _number(ratio), None, None)
                  for stamp, ratio in frame["Stock Splits"].items() if ratio]
    source = _source("yfinance", f"https://finance.yahoo.com/quote/{encode_path(ticker, safe='')}/history/")
    return _yahoo_result(ticker, period, interval, as_of, rows, meta, source, splits)


def _tencent_symbol(ticker):
    if ticker.endswith(".HK"):
        return f"hk{ticker.removesuffix('.HK').zfill(5)}"
    if ticker.endswith((".SS", ".SZ")):
        return ("sh" if ticker.endswith(".SS") else "sz") + ticker.split(".")[0]
    if _equity_market(ticker)[0] == "US" and "-" not in ticker:
        return f"us{ticker}"
    return None


def _tencent_quote(ticker, code, metadata):
    payload, url = _http(TENCENT_QUOTE + code, referer="https://gu.qq.com/")
    match = re.fullmatch(r'v_[A-Za-z0-9]+="([^"]*)";\s*', payload.decode("gb18030").strip())
    fields = match.group(1).split("~") if match else []
    if len(fields) <= 37:
        raise ProviderError("Tencent quote returned no recognizable data")
    zone = _zone(metadata["timezone"])
    stamp = None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S", "%Y%m%d%H%M%S"):
        try:
            stamp = datetime.strptime(fields[30], fmt).replace(tzinfo=zone or timezone.utc)
            break
        except ValueError:
            continue
    if not stamp or stamp > _now() + timedelta(minutes=2):
        raise ProviderError("Tencent quote has no valid observation timestamp")
    market = metadata["market"]
    amount = _number(fields[37])
    volume = _number(fields[36])
    if market == "US":
        currency = fields[35]
        if not re.fullmatch(r"[A-Z]{3}", currency):
            raise ProviderError("Tencent US quote currency is missing or invalid")
        metadata.update(company_name=fields[46] if len(fields) > 46 and fields[46] else fields[1],
                        currency=currency, price_unit=currency)
        venue = fields[2].split(".")[-1]
        metadata["market"] = {"OQ": "Nasdaq", "N": "NYSE", "A": "NYSE American"}.get(venue, "US")
    else:
        metadata["company_name"] = fields[1]
    volume_unit = "shares"
    if market in {"SSE", "SZSE"}:
        volume_unit = "lots_100"
        observed_trade = fields[35].split("/")
        if len(observed_trade) >= 3:
            amount = _number(observed_trade[2])
        else:
            amount = amount * 10_000 if amount is not None else None
    now = _now().astimezone(zone or timezone.utc)
    session_close = {"US": clock_time(16), "HKEX": clock_time(16),
                     "SSE": clock_time(15), "SZSE": clock_time(15)}[market]
    closed = stamp.time() >= session_close
    kind = "historical_close" if closed else "historical_observation" if stamp.date() < now.date() else "freshness_unverified"
    result = _quote(
        ticker, fields[3], _iso(stamp), kind,
        metadata, _source("tencent", url), volume=volume, change=fields[32], turnover=amount,
        previous=fields[4], change_absolute=fields[31],
        trading_status="completed_session" if closed else None, volume_unit=volume_unit,
    )
    if result:
        result["price_basis"] = "regular_market_price"
    return result


def _tencent_history(ticker, code, period, as_of, metadata):
    zone = _zone(metadata["timezone"])
    start, end = _window(as_of, period, zone)
    cursor = end.date()
    bars = []
    mode = None
    first_url = None
    # The upstream service returns the newest 500 bars in the request. Move
    # backwards so a multi-year window cannot silently become its newest page.
    for _ in range(12):
        payload, url = _http(TENCENT_HISTORY, params={"param": f"{code},day,{start},{cursor},500,qfq"},
                             referer="https://web.ifzq.gtimg.cn/")
        document = json.loads(payload)
        result = (document.get("data") or {}).get(code) or {}
        if document.get("code") not in (None, 0):
            raise ProviderError("Tencent history returned a provider error")
        selected = "provider_forward" if result.get("qfqday") else "none"
        page = result.get("qfqday") or result.get("day") or []
        if not page:
            break
        if selected != mode and mode is not None:
            raise ProviderError("Tencent history changed its price basis between pages")
        mode = selected
        first_url = first_url or url
        dates = []
        for row in page:
            if len(row) < 6:
                continue
            try:
                day = date.fromisoformat(row[0])
            except ValueError:
                continue
            dates.append(day)
            if day < start or day > cursor:
                continue
            prices = dict(zip(("open", "close", "high", "low"), row[1:5]))
            moment = datetime.combine(day, clock_time(), tzinfo=zone or timezone.utc)
            complete = _complete(moment, end, "1d")
            if as_of is None or complete:
                bars.append(_bar(day.isoformat(), prices, row[5], complete=complete))
        if not dates:
            raise ProviderError("Tencent history returned no valid trade dates")
        next_cursor = min(dates) - timedelta(days=1)
        if next_cursor < start or len(page) < 500:
            break
        if next_cursor >= cursor:
            raise ProviderError("Tencent history pagination stopped advancing")
        cursor = next_cursor
    else:
        raise ProviderError("Tencent history exceeds its bounded window; choose a shorter period")
    if not first_url:
        return None
    unit = "shares" if ticker.endswith(".HK") else "lots_100"
    return _history(bars, period, "1d", mode, metadata, _source("tencent", first_url), unit)


def _tencent(ticker, period, interval, as_of, need_quote, need_history):
    code = _tencent_symbol(ticker)
    if not code or (not need_quote and (interval != "1d" or code.startswith("us"))):
        raise ProviderError("Tencent does not cover the requested symbol or interval")
    market, currency, zone = _equity_market(ticker)
    metadata = {"market": market, "currency": currency, "price_unit": currency, "timezone": zone,
                "company_name": None, "instrument_type": "EQUITY"}
    quote = None
    history = None
    failures = []
    if need_quote and as_of is None:
        try:
            quote = _tencent_quote(ticker, code, metadata)
        except ProviderError as exc:
            failures.append(str(exc))
    if need_history and interval == "1d" and not code.startswith("us"):
        try:
            history = _tencent_history(ticker, code, period, as_of, metadata)
        except ProviderError as exc:
            failures.append(str(exc))
    if not quote and not history and failures:
        raise ProviderError("; ".join(failures))
    return quote, history


def _daily_rows(ticker, period, as_of, provider, url, records, column_map):
    market, currency, zone_name = _equity_market(ticker)
    metadata = {"market": market, "currency": currency, "price_unit": currency, "timezone": zone_name,
                "company_name": None, "instrument_type": "EQUITY"}
    zone = _zone(zone_name)
    start, end = _window(as_of, period, zone)
    bars = []
    for record in records:
        if not isinstance(record, dict):
            continue
        try:
            day = date.fromisoformat(record[column_map["date"]])
        except (TypeError, ValueError, KeyError):
            continue
        if not start <= day <= end.date():
            continue
        moment = datetime.combine(day, clock_time(), tzinfo=zone or timezone.utc)
        complete = _complete(moment, end, "1d")
        if as_of is not None and not complete:
            continue
        bars.append(_bar(
            day.isoformat(), {key: record.get(column_map[key]) for key in PRICE_FIELDS},
            record.get(column_map["volume"]), turnover=record.get(column_map.get("turnover")),
            complete=complete,
        ))
    return _history(bars, period, "1d", "unknown", metadata, _source(provider, url), "shares")


def _sina(ticker, period, interval, as_of, need_quote, need_history):
    if _equity_market(ticker)[0] != "US" or interval != "1d" or "-" in ticker:
        raise ProviderError("Sina covers bare US tickers and daily bars only")
    body, url = _http(SINA, params={"symbol": ticker}, referer="https://stock.finance.sina.com.cn/")
    match = re.search(r"var\s+x\s*=\s*\(?(\[.*\])", body.decode("utf-8-sig"), re.DOTALL)
    if not match:
        raise ProviderError("Sina response contains no JSONP bar array")
    records = json.loads(match.group(1))
    columns = {"date": "d", "open": "o", "high": "h", "low": "l", "close": "c", "volume": "v", "turnover": "a"}
    return None, _daily_rows(ticker, period, as_of, "sina", url, records, columns)


def _stooq(ticker, period, interval, as_of, need_quote, need_history):
    if _equity_market(ticker)[0] != "US" or interval != "1d":
        raise ProviderError("Stooq adapter covers US stocks and daily bars only")
    start, end = _window(as_of, period, _zone("America/New_York"))
    params = {"s": ticker.lower() + ".us", "i": "d", "d1": start.strftime("%Y%m%d"),
              "d2": end.strftime("%Y%m%d")}
    body, url = _http(STOOQ, params=params)
    csv_text = body.decode("utf-8-sig")
    if csv_text.lstrip().startswith("<"):
        raise ProviderError("Stooq returned an anti-bot page instead of CSV")
    columns = {key.lower(): key for key in ("Date", "Open", "High", "Low", "Close", "Volume")}
    records = csv.DictReader(io.StringIO(csv_text))
    if not set(columns.values()).issubset(records.fieldnames or ()):
        raise ProviderError("Stooq returned no recognized CSV data; possible quota limit")
    return None, _daily_rows(ticker, period, as_of, "stooq", url, records, columns)


_PROVIDERS = {"yahoo": _yahoo, "yfinance": _yfinance, "tencent": _tencent, "sina": _sina, "stooq": _stooq}
PROVIDERS = tuple(_PROVIDERS)


def _configured_providers(providers):
    if providers is None:
        configured = os.environ.get("VIBE_STOCK_MARKET_PROVIDERS", "").strip()
        providers = configured.split(",") if configured else _DEFAULT_PROVIDERS
    if isinstance(providers, str):
        providers = providers.split(",")
    if not isinstance(providers, Sequence) or not providers:
        raise ValueError("providers must be one or more market provider names")
    names = tuple(dict.fromkeys(str(name).strip().lower() for name in providers))
    if any(name not in _PROVIDERS for name in names):
        raise ValueError(f"unknown market provider; use {', '.join(PROVIDERS)}")
    return names


def _fetch(symbol, period, interval, providers, as_of, *, include_quote=True, include_history=True):
    ticker = normalize_symbol(symbol)
    if period not in PERIODS:
        raise ValueError(f"unsupported period: {period}")
    if interval not in INTERVALS:
        raise ValueError(f"unsupported interval: {interval}")
    if as_of is not None:
        _window(as_of, period, _zone(_equity_market(ticker)[2]))
    errors = []
    best_quote = None
    best_history = None
    close_quote = None
    for name in _configured_providers(providers):
        want_quote = include_quote and best_quote is None and as_of is None
        want_history = best_history is None and (include_history or (include_quote and best_quote is None))
        if not want_quote and not want_history:
            break
        try:
            candidate_quote, candidate_history = _PROVIDERS[name](
                ticker, period, interval, as_of, want_quote, want_history
            )
            if want_quote and candidate_quote:
                best_quote = candidate_quote
            if want_history and candidate_history:
                best_history = candidate_history
            if candidate_history and close_quote is None:
                close_quote = _completed_close_quote(ticker, candidate_history)
            if not ((want_quote and candidate_quote) or (want_history and candidate_history)):
                raise ProviderError("no data for the requested symbol, period or interval")
            if as_of is not None and (not include_history or best_history):
                break
        except Exception as exc:
            # Public feeds and yfinance raise heterogeneous transport/parser
            # exceptions. Keep other providers usable and retain the cause.
            operation = "market_data" if want_quote and want_history else "quote" if want_quote else "history"
            detail = " ".join(str(exc).split())[:300]
            errors.append({"provider": name, "operation": operation,
                           "message": detail or type(exc).__name__})
    best_quote = best_quote or close_quote
    requested = {}
    if include_quote:
        requested["quote"] = best_quote
    if include_history:
        requested["history"] = best_history
    available = sum(value is not None for value in requested.values())
    status = "ok" if available == len(requested) else "partial" if available else "unavailable"
    return {"symbol": ticker, "status": status, **requested, "errors": errors}


def market_data(symbol: str, period: str = "1y", interval: str = "1d", providers=None, as_of=None) -> dict:
    """Fetch quote and OHLCV history; an explicit as_of restricts to a past observation window."""
    return _fetch(symbol, period, interval, providers, as_of)


def history(symbol: str, period: str = "1y", interval: str = "1d", providers=None, as_of=None) -> dict:
    """Fetch OHLCV history without a separate quote lookup."""
    return _fetch(symbol, period, interval, providers, as_of, include_quote=False)


def quote(symbol: str, providers=None, as_of=None) -> dict:
    """Fetch a regular-session quote, with a labeled historical-close fallback."""
    return _fetch(symbol, "5d", "1d", providers, as_of, include_history=False)
