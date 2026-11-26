"""Issuer financials, valuation and consensus for vibe-stock-analysis.

SEC ticker mapping, GAAP alias ordering and fiscal span separation are adapted
from Vibe-Trading, Copyright (c) 2026 Vibe-Trading Contributors (MIT).
See LICENSE and references/upstream.md in the distribution.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from functools import lru_cache
import json
import math
import os
import re
import shutil
import ssl
import subprocess
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import Request, urlopen

SEC_TICKERS = "https://www.sec.gov/files/company_tickers.json"
SEC_TICKER_SEARCH = "https://efts.sec.gov/LATEST/search-index"
SEC_FACTS = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
SEC_SUBMISSIONS = "https://data.sec.gov/submissions/CIK{cik}.json"
SEC_ARCHIVE = "https://www.sec.gov/Archives/edgar/data"
NASDAQ_EPS_FORECAST = "https://api.nasdaq.com/api/analyst/{symbol}/earnings-forecast"
SEC_FORMS = frozenset({"10-K", "10-Q", "20-F", "40-F", "6-K"})
# SEC's www host can block an IP that can still read data.sec.gov. These
# established CIKs allow a small set of common requests to survive that outage.
SEC_KNOWN_CIKS = {
    "NVDA": "0001045810", "MSFT": "0000789019", "AAPL": "0000320193",
    "TSLA": "0001318605", "GOOG": "0001652044", "GOOGL": "0001652044",
    "AMD": "0000002488", "AVGO": "0001730168", "INTC": "0000050863",
    "TSM": "0001046179", "AMZN": "0001018724", "META": "0001326801",
}
SEC_SPACING = 0.15
SEC_UA_DEFAULT = "vibe-stock-analysis/1.0 (read-only financial research CLI)"
_SEC_LOCK = threading.Lock()
_SEC_LAST_REQUEST = 0.0
_MAX_RESPONSE_BYTES = 32 * 1024 * 1024

# Ordered concepts are adapted from agent/backtest/loaders/_fundamental_schema.py.
FLOW_CONCEPTS = {
    "revenue": ("RevenueFromContractWithCustomerExcludingAssessedTax",
                "RevenueFromContractWithCustomerIncludingAssessedTax", "Revenues", "SalesRevenueNet"),
    "cost_of_revenue": ("CostOfGoodsAndServicesSold", "CostOfRevenue", "CostOfGoodsSold"),
    "gross_profit": ("GrossProfit",),
    "operating_income": ("OperatingIncomeLoss",),
    "net_income": ("NetIncomeLoss", "ProfitLoss"),
    "operating_cash_flow": ("NetCashProvidedByUsedInOperatingActivities",
                            "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations"),
    "capex": ("PaymentsToAcquirePropertyPlantAndEquipment", "PaymentsToAcquireProductiveAssets",
              "PaymentsToAcquirePropertyPlantAndEquipmentAndIntangibleAssets"),
}
BALANCE_CONCEPTS = {
    "assets": ("Assets",),
    "current_assets": ("AssetsCurrent",),
    "liabilities": ("Liabilities",),
    "current_liabilities": ("LiabilitiesCurrent",),
    "equity": ("StockholdersEquity",),
    "cash": ("CashAndCashEquivalentsAtCarryingValue", "CashAndCashEquivalents"),
    "debt_combined": ("LongTermDebtAndFinanceLeaseObligations", "LongTermDebtAndCapitalLeaseObligations", "LongTermDebt"),
    "debt_current": ("DebtCurrent", "LongTermDebtAndFinanceLeaseObligationsCurrent", "LongTermDebtCurrent"),
    "debt_noncurrent": ("LongTermDebtAndFinanceLeaseObligationsNoncurrent", "LongTermDebtNoncurrent"),
    "shares_outstanding": ("CommonStockSharesOutstanding",),
}
IFRS_CONCEPTS = {
    "revenue": ("Revenue",), "cost_of_revenue": ("CostOfSales",), "gross_profit": ("GrossProfit",),
    "operating_income": ("ProfitLossFromOperatingActivities",),
    "net_income": ("ProfitLossAttributableToOwnersOfParent", "ProfitLoss"),
    "operating_cash_flow": ("CashFlowsFromUsedInOperatingActivities",),
    "capex": ("PurchaseOfPropertyPlantAndEquipment",), "assets": ("Assets",),
    "current_assets": ("CurrentAssets",), "liabilities": ("Liabilities",),
    "current_liabilities": ("CurrentLiabilities",), "equity": ("EquityAttributableToOwnersOfParent",),
    "cash": ("CashAndCashEquivalents",),
    "shares_outstanding": ("NumberOfSharesOutstanding",),
}
YAHOO_STATEMENTS = {
    "get_income_stmt": {"revenue": ("TotalRevenue", "OperatingRevenue"), "cost_of_revenue": ("CostOfRevenue",),
                        "gross_profit": ("GrossProfit",), "operating_income": ("OperatingIncome",),
                        "net_income": ("NetIncome", "NetIncomeCommonStockholders")},
    "get_balance_sheet": {"assets": ("TotalAssets",), "current_assets": ("CurrentAssets",),
                          "liabilities": ("TotalLiabilitiesNetMinorityInterest",), "current_liabilities": ("CurrentLiabilities",),
                          "equity": ("StockholdersEquity",), "cash": ("CashAndCashEquivalents",),
                          "debt_reported": ("TotalDebt",)},
    "get_cash_flow": {"operating_cash_flow": ("OperatingCashFlow",), "capex": ("CapitalExpenditure",)},
}


class ProviderError(RuntimeError):
    """A data provider failed to return usable data."""


def utc_now():
    return datetime.now(timezone.utc)


def day(value):
    return value if type(value) is date else date.fromisoformat(value)


def number(value):
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return result if math.isfinite(result) else None


def ratio(numerator, denominator):
    if numerator is None or denominator is None or denominator <= 0:
        return None
    return numerator / denominator


@lru_cache(maxsize=1)
def _tls_context():
    # Native trust allows a managed macOS or Windows workstation's approved
    # enterprise CA to work. Python's default CA validation remains available.
    if not (os.environ.get("SSL_CERT_FILE") or os.environ.get("SSL_CERT_DIR")):
        try:
            import truststore
            return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        except (ImportError, OSError, RuntimeError):
            pass
    return ssl.create_default_context()


def _curl_bytes(url, headers):
    executable = shutil.which("curl")
    if not executable:
        raise ProviderError("system certificate validation failed and curl is unavailable")
    # Match urllib's HTTP/1.1 on the system-trust path. Some HTTPS proxies
    # abort curl's negotiated HTTP/2 streams for the FRED CSV endpoints.
    args = [executable, "--disable", "--http1.1", "--fail", "--location",
            "--max-redirs", "3", "--silent", "--show-error",
            "--connect-timeout", "6", "--max-time", "15", "--max-filesize", str(_MAX_RESPONSE_BYTES),
            "--proto", "=https", "--proto-redir", "=https"]
    for name, value in headers.items():
        args.extend(["--header", f"{name}: {value}"])
    try:
        result = subprocess.run([*args, "--", url], capture_output=True, timeout=16, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ProviderError(f"system curl failed ({type(exc).__name__})") from exc
    if result.returncode:
        status = re.search(rb"returned error: (\d{3})", result.stderr)
        if status:
            raise ProviderError(f"HTTP {status.group(1).decode('ascii')}")
        raise ProviderError(f"system curl transfer failed (exit {result.returncode})")
    return result.stdout


def _read_bytes(url, headers):
    hostname = urlsplit(url).hostname or ""
    if urlsplit(url).scheme != "https":
        raise ProviderError("provider URLs must use HTTPS")
    try:
        request = Request(url, headers=headers)
        with urlopen(request, timeout=10, context=_tls_context()) as response:
            payload = response.read(_MAX_RESPONSE_BYTES + 1)
    except HTTPError as exc:
        raise ProviderError(f"HTTP {exc.code}") from exc
    except (URLError, OSError) as exc:
        if isinstance(getattr(exc, "reason", exc), ssl.SSLCertVerificationError):
            payload = _curl_bytes(url, headers)
        else:
            raise ProviderError(f"{hostname} request failed ({type(getattr(exc, 'reason', exc)).__name__})") from exc
    if len(payload) > _MAX_RESPONSE_BYTES:
        raise ProviderError("provider payload exceeds the limit")
    return payload


def get_bytes(url, *, user_agent=None, sec=False, accept="application/json"):
    """Read a fixed HTTPS provider endpoint with bounded retries and SEC pacing."""
    global _SEC_LAST_REQUEST
    agent = user_agent if user_agent is not None else (
        SEC_UA_DEFAULT if sec else "Mozilla/5.0 (compatible; vibe-stock-analysis/1.0)"
    )
    if "\r" in agent or "\n" in agent:
        raise ValueError("invalid user agent")
    headers = {"Accept": accept}
    if agent:
        headers["User-Agent"] = agent
    for attempt in range(2):
        if sec:
            with _SEC_LOCK:
                time.sleep(max(0.0, SEC_SPACING - (time.monotonic() - _SEC_LAST_REQUEST)))
                _SEC_LAST_REQUEST = time.monotonic()
        try:
            return _read_bytes(url, headers)
        except ProviderError as exc:
            if attempt or str(exc) not in {"HTTP 429", "HTTP 500", "HTTP 502", "HTTP 503", "HTTP 504"}:
                raise
            time.sleep(1.0)


def get_json(url, *, user_agent=None, sec=False):
    try:
        value = json.loads(get_bytes(url, user_agent=user_agent, sec=sec))
    except (ValueError, UnicodeError) as exc:
        raise ProviderError("provider returned invalid JSON") from exc
    if not isinstance(value, dict):
        raise ProviderError("provider returned an unexpected JSON structure")
    return value


def sec_contact(user_agent):
    return user_agent or os.environ.get("VIBE_STOCK_SEC_USER_AGENT") or SEC_UA_DEFAULT


def sec_ticker(symbol):
    value = symbol.upper().removesuffix(".US")
    if re.fullmatch(r"[A-Z]+\.[A-Z]", value) and not value.endswith((".L", ".T", ".V", ".F")):
        value = value.replace(".", "-")
    return value if re.fullmatch(r"[A-Z][A-Z0-9]{0,8}(?:-[A-Z])?", value) else None


@lru_cache(maxsize=8)
def _ticker_table(user_agent):
    records = get_json(SEC_TICKERS, user_agent=user_agent, sec=True)
    mapping = {}
    for row in records.values():
        if not isinstance(row, dict) or not row.get("ticker"):
            continue
        cik = str(row.get("cik_str", ""))
        if cik.isdigit() and len(cik) <= 10:
            mapping[str(row["ticker"]).strip().upper()] = cik.zfill(10)
    if not mapping:
        raise ProviderError("SEC ticker table contained no ticker mappings")
    return mapping


@lru_cache(maxsize=64)
def _ticker_search(ticker, user_agent):
    url = f"{SEC_TICKER_SEARCH}?{urlencode({'keysTyped': ticker})}"
    payload = get_json(url, user_agent=user_agent, sec=True)
    matches = set()
    for hit in payload.get("hits", {}).get("hits", []):
        tickers = hit.get("_source", {}).get("tickers", "")
        cik = str(hit.get("_id", ""))
        if ticker in {value.strip().upper() for value in tickers.split(",")} and cik.isdigit() and len(cik) <= 10:
            matches.add(cik.zfill(10))
    if len(matches) > 1:
        raise ProviderError("SEC search returned more than one exact ticker match")
    return next(iter(matches), None)


def get_cik(symbol, user_agent=None):
    ticker = sec_ticker(symbol)
    if ticker is None:
        return None
    if ticker in SEC_KNOWN_CIKS:
        return SEC_KNOWN_CIKS[ticker]
    agent = sec_contact(user_agent)
    try:
        return _ticker_table(agent).get(ticker)
    except ProviderError as exc:
        try:
            return _ticker_search(ticker, agent)
        except ProviderError as fallback:
            raise ProviderError(f"SEC ticker table ({exc}); SEC exact ticker search ({fallback})") from fallback


@lru_cache(maxsize=12)
def fetch_submissions(cik, user_agent):
    return get_json(SEC_SUBMISSIONS.format(cik=cik), user_agent=sec_contact(user_agent), sec=True)


@lru_cache(maxsize=12)
def fetch_facts(cik, user_agent):
    return get_json(SEC_FACTS.format(cik=cik), user_agent=sec_contact(user_agent), sec=True)


def filing_url(cik, accession, filename=None):
    if not re.fullmatch(r"\d{10}-\d{2}-\d{6}", accession or ""):
        return None
    basename = filename or f"{accession}-index.htm"
    if not re.fullmatch(r"[\w.\-]+", basename) or basename in {".", ".."}:
        return None
    return f"{SEC_ARCHIVE}/{int(cik)}/{accession.replace('-', '')}/{quote(basename)}"


def _fact_entries(concepts, aliases, unit_pattern, cutoff, since):
    for preference, alias in enumerate(aliases):
        for unit, values in concepts.get(alias, {}).get("units", {}).items():
            if not re.fullmatch(unit_pattern, unit):
                continue
            for row in values:
                filed, end, start = row.get("filed"), row.get("end"), row.get("start")
                if (row.get("form", "").removesuffix("/A") not in SEC_FORMS or not filed or not end
                        or filed > cutoff or end > cutoff or end < since or number(row.get("val")) is None):
                    continue
                try:
                    if day(filed) < day(end) or (start and day(start) > day(end)):
                        continue
                except (TypeError, ValueError):
                    continue
                yield (start, end, unit), preference, {"value": row["val"], "filed_on": filed,
                                                        "accession": row.get("accn"), "concept": alias}


def _reporting_currency(concepts, aliases, cutoff, since):
    annual = []
    recent = []
    for (start, end, unit), preference, row in _fact_entries(concepts, aliases, r"[A-Z]{3}", cutoff, since):
        if start:
            rank = (end, row["filed_on"], -preference, unit == "USD", unit)
            recent.append(rank)
            if 330 <= (day(end) - day(start)).days <= 380:
                annual.append(rank)
    valid = annual or recent
    return max(valid)[-1] if valid else None


def _select_facts(concepts, aliases, currency, cutoff, since, *, first_filed=False):
    result = {}
    ranks = {}
    first_dates = {}
    for (start, end, _), preference, row in _fact_entries(concepts, aliases, currency, cutoff, since):
        key = (start, end)
        ordinal = day(row["filed_on"]).toordinal()
        rank = (ordinal if first_filed else -ordinal, preference)
        first_dates[key] = min(first_dates.get(key, row["filed_on"]), row["filed_on"])
        if key not in ranks or rank < ranks[key]:
            result[key] = row
            ranks[key] = rank
    for key, fact in result.items():
        fact["first_filed_on"] = first_dates[key]
    return result


def _quarter_cash_flows(frames, fiscal_starts):
    """Identify true quarters by fiscal span; derive YTD differences when possible."""
    quarters = {}
    by_start = defaultdict(list)
    for (start, end), fact in frames.items():
        if not start:
            continue
        if 60 <= (day(end) - day(start)).days <= 120:
            quarters[end] = (start, fact)
        if start in fiscal_starts:
            by_start[start].append((end, fact))
    for series in by_start.values():
        series.sort()
        for (previous_end, previous), (end, accumulated) in zip(series, series[1:]):
            if end in quarters or not 60 <= (day(end) - day(previous_end)).days <= 120:
                continue
            if previous["concept"] != accumulated["concept"]:
                continue
            start = (day(previous_end) + timedelta(days=1)).isoformat()
            facts = [previous, accumulated]
            quarters[end] = (start, _derived(accumulated["value"] - previous["value"], facts, "current YTD minus preceding YTD"))
    # Some issuers supply only three discrete quarters plus the full fiscal year.
    # Derive Q4 only when all three preceding periods align and share the tag.
    for (start, end), annual in frames.items():
        if not start or end in quarters or not 330 <= (day(end) - day(start)).days <= 380:
            continue
        candidates = sorted((quarter_start, quarter_end, fact) for quarter_end, (quarter_start, fact) in quarters.items()
                            if start <= quarter_start < quarter_end < end)
        if len(candidates) != 3 or candidates[0][0] != start:
            continue
        if any((day(right[0]) - day(left[1])).days != 1 for left, right in zip(candidates, candidates[1:])):
            continue
        parts = [fact for _, _, fact in candidates]
        if any(fact.get("concept") != annual["concept"] for fact in parts):
            continue
        preceding_end = day(candidates[-1][1])
        if 60 <= (day(end) - preceding_end).days <= 120:
            quarter_start = (preceding_end + timedelta(days=1)).isoformat()
            quarters[end] = (quarter_start, _derived(annual["value"] - sum(fact["value"] for fact in parts),
                                                       [annual, *parts], "full fiscal year less three discrete quarters"))
    return quarters


def _derived(value, facts, method):
    return {"value": value, "filed_on": max(fact["filed_on"] for fact in facts), "method": method,
            "first_filed_on": max(fact["first_filed_on"] for fact in facts),
            "accessions": sorted({accession for fact in facts for accession in
                                  ([fact["accession"]] if fact.get("accession") else fact.get("accessions", []))})}


def _financial_rows(series, balance, cadence):
    rows = {}
    for field, frames in series.items():
        for end, (start, fact) in frames.items():
            row = rows.setdefault((start, end), {"start": start, "end": end, "facts": {}, "cadence": cadence})
            row["facts"][field] = fact
    ordered = sorted(rows.values(), key=lambda row: (row["end"], len(row["facts"]), row["start"]))
    # Prefer the coherent frame with revenue and net income when providers
    # publish overlapping 52/53-week comparative spans.
    selected = {}
    def importance(entry):
        return ("revenue" in entry["facts"], "net_income" in entry["facts"], len(entry["facts"]))
    for row in ordered:
        end = row["end"]
        if end not in selected or importance(row) >= importance(selected[end]):
            selected[end] = row
    for period in selected.values():
        for field, frames in balance.items():
            fact = frames.get((None, period["end"]))
            if fact:
                period["facts"][field] = fact
        _calculate_row(period)
    return list(selected.values())


def _calculate_row(row):
    facts = row["facts"]
    values = {name: fact["value"] for name, fact in facts.items() if not name.startswith("debt_")}
    if "gross_profit" not in values and all(field in values for field in ("revenue", "cost_of_revenue")):
        values["gross_profit"] = values["revenue"] - values["cost_of_revenue"]
    if "liabilities" not in values and "assets" in values and "equity" in values:
        values["total_assets_minus_parent_equity"] = values["assets"] - values["equity"]
    if all(field in values for field in ("operating_cash_flow", "capex")):
        values["free_cash_flow"] = values["operating_cash_flow"] - values["capex"]
    if "debt_current" in facts and "debt_noncurrent" in facts:
        values["debt"] = facts["debt_current"]["value"] + facts["debt_noncurrent"]["value"]
    elif "debt_reported" in facts:
        values["debt"] = facts["debt_reported"]["value"]
    elif "debt_combined" in facts:
        values["reported_long_term_debt_including_current_portion_when_reported"] = facts["debt_combined"]["value"]
    values["gross_margin"] = ratio(values.get("gross_profit"), values.get("revenue"))
    values["net_margin"] = ratio(values.get("net_income"), values.get("revenue"))
    values["operating_margin"] = ratio(values.get("operating_income"), values.get("revenue"))
    values["current_ratio"] = ratio(values.get("current_assets"), values.get("current_liabilities"))
    row["metrics"] = values
    row["filed_on"] = max((fact["filed_on"] for fact in facts.values() if fact.get("filed_on")), default=None)
    headline_facts = [facts[field] for field in ("revenue", "net_income") if field in facts]
    row["headline_facts_first_filed_on"] = max((fact["first_filed_on"] for fact in headline_facts if fact.get("first_filed_on")),
                                               default=None)


def _growth_and_returns(rows, balance):
    for row in rows:
        current = row["metrics"]
        end = day(row["end"])
        prior_candidates = [previous for previous in rows if abs((end - day(previous["end"])).days - 365) <= 14]
        prior = min(prior_candidates, key=lambda previous: abs((end - day(previous["end"])).days - 365), default=None)
        for field in ("revenue", "net_income"):
            prior_value = prior["metrics"].get(field) if prior else None
            divided = ratio(current.get(field), prior_value)
            current[f"{field}_yoy"] = divided - 1 if divided is not None else None
        if row["cadence"] == "quarterly":
            continue
        previous_end = (day(row["start"]) - timedelta(days=1)).isoformat()
        for name, field in (("roe", "equity"), ("roa", "assets")):
            opening = balance.get(field, {}).get((None, previous_end), {}).get("value")
            closing = current.get(field)
            average = (opening + closing) / 2 if opening is not None and closing is not None else None
            current[name] = ratio(current.get("net_income"), average)


def _trailing_rows(quarters):
    result = []
    for i in range(4, len(quarters) + 1):
        group = quarters[i - 4:i]
        duration = (day(group[-1]["end"]) - day(group[0]["start"])).days
        if not 330 <= duration <= 380 or any(
            (day(right["start"]) - day(left["end"])).days != 1 for left, right in zip(group, group[1:])
        ):
            continue
        facts = {field: fact for field, fact in group[-1]["facts"].items() if field in BALANCE_CONCEPTS}
        for field in FLOW_CONCEPTS:
            components = [row["facts"].get(field) for row in group]
            if all(components):
                facts[field] = _derived(sum(fact["value"] for fact in components), components, "four consecutive fiscal quarters")
        if not any(field in facts for field in ("revenue", "net_income")):
            continue
        row = {"start": group[0]["start"], "end": group[-1]["end"], "facts": facts, "cadence": "ttm"}
        _calculate_row(row)
        result.append(row)
    return result


def _parse_sec_facts(payload, cut, *, first_filed=False):
    namespaces = payload.get("facts", {})
    cutoff = cut.isoformat()
    since = (cut - timedelta(days=365 * 9)).isoformat()
    year_since = (cut - timedelta(days=365 * 3)).isoformat()
    concepts = namespaces.get("us-gaap") or namespaces.get("ifrs-full") or {}
    schema = "US GAAP" if namespaces.get("us-gaap") else "IFRS"
    aliases = {**FLOW_CONCEPTS, **BALANCE_CONCEPTS} if schema == "US GAAP" else IFRS_CONCEPTS
    currency = _reporting_currency(concepts, aliases["revenue"], cutoff, year_since)
    if not currency:
        raise ProviderError("no supported reporting currency and revenue period found in SEC XBRL")
    flows = {field: _select_facts(concepts, aliases.get(field, ()), currency, cutoff, since, first_filed=first_filed)
             for field in FLOW_CONCEPTS}
    balance = {field: _select_facts(concepts, aliases.get(field, ()), "shares" if field == "shares_outstanding" else currency,
                                    cutoff, since, first_filed=first_filed) for field in BALANCE_CONCEPTS}
    annual = {
        field: {end: (start, fact) for (start, end), fact in frames.items()
                if start and 330 <= (day(end) - day(start)).days <= 380}
        for field, frames in flows.items()
    }
    fiscal_starts = {start for frames in annual.values() for start, _ in frames.values()}
    fiscal_starts |= {(day(end) + timedelta(days=1)).isoformat() for frames in annual.values() for end in frames}
    quarter = {field: _quarter_cash_flows(frames, fiscal_starts) for field, frames in flows.items()}
    annual_rows = _financial_rows(annual, balance, "annual")
    quarter_rows = _financial_rows(quarter, balance, "quarterly")
    trailing_rows = _trailing_rows(quarter_rows)
    for rows in (annual_rows, quarter_rows, trailing_rows):
        _growth_and_returns(rows, balance)
    if not (annual_rows or quarter_rows):
        raise ProviderError("no coherent current SEC financial periods")
    cover_shares = _select_facts(namespaces.get("dei", {}), ("EntityCommonStockSharesOutstanding",), "shares",
                                 cutoff, year_since, first_filed=first_filed)
    shares_pair = max(cover_shares.items(), key=lambda item: item[0][1], default=(None, None))
    shares = {"as_of": shares_pair[0][1], **shares_pair[1]} if shares_pair[0] else None
    return {"provider": "SEC EDGAR", "currency": currency, "accounting_standard": schema,
            "annual": annual_rows[-5:][::-1], "quarterly": quarter_rows[-9:][::-1],
            "ttm": trailing_rows[-1] if trailing_rows else None, "shares_outstanding_cover_page": shares,
            "units": {"statement_amounts": currency, "margins_and_growth": "decimal ratio", "shares": "shares"}}


def _yahoo_data(symbol):
    try:
        import yfinance as yf
    except ImportError as exc:
        raise ProviderError("optional yfinance package is unavailable") from exc
    try:
        ticker = yf.Ticker(symbol)
        info = ticker.get_info()
    except Exception as exc:
        # Provider exceptions can include endpoint URLs. Do not propagate them.
        raise ProviderError(f"Yahoo company data failed ({type(exc).__name__})") from exc
    if not isinstance(info, dict) or not any(field in info for field in ("longName", "marketCap", "sector", "totalRevenue")):
        raise ProviderError("Yahoo returned no company financial data")
    return info


def _profile(sec_profile, sec_facts, yahoo_info):
    profile = {"name": sec_profile.get("name") or sec_facts.get("entityName") or yahoo_info.get("longName"),
               "market": ", ".join(str(exchange) for exchange in sec_profile.get("exchanges", []) if exchange) or
                         yahoo_info.get("fullExchangeName"),
               "sic": sec_profile.get("sic"), "sic_description": sec_profile.get("sicDescription"),
               "sector": yahoo_info.get("sector"), "industry": yahoo_info.get("industry"),
               "website": sec_profile.get("website") or yahoo_info.get("website"),
               "investor_website": sec_profile.get("investorWebsite") or None,
               "business_description": yahoo_info.get("longBusinessSummary") or sec_profile.get("description") or None}
    return profile if any(profile.values()) else None


def _valuation(financials, yahoo_info, market_cap, market_currency, market_price, price_as_of, single_listing):
    yahoo_cap = number(yahoo_info.get("marketCap"))
    amounts = financials.get("ttm") if financials else None
    values = amounts["metrics"] if amounts else {}
    currency = financials.get("currency") if financials else None
    supplied_cap = number(market_cap)
    cap_source = None
    share_date = None
    valuation_date = price_as_of.isoformat() if isinstance(price_as_of, (date, datetime)) else price_as_of
    if supplied_cap is not None and supplied_cap > 0 and market_currency:
        cap = supplied_cap
        cap_currency = market_currency
        cap_source = "caller-supplied market capitalization"
    elif yahoo_cap is not None and yahoo_cap > 0 and yahoo_info.get("currency"):
        cap = yahoo_cap
        cap_currency = yahoo_info["currency"]
        cap_source = "Yahoo Finance marketCap"
        # The Yahoo quote timestamp can differ from the supplied market quote.
        stamp = number(yahoo_info.get("regularMarketTime"))
        try:
            valuation_date = datetime.fromtimestamp(stamp, tz=timezone.utc).isoformat() if stamp is not None else None
        except (ValueError, OverflowError, OSError):
            valuation_date = None
    else:
        cap = None
        cap_currency = market_currency
        shares = financials.get("shares_outstanding_cover_page") if financials else None
        price = number(market_price)
        if (single_listing and shares and price is not None and price > 0 and valuation_date and
                market_currency == currency == "USD"):
            quote_date = day(valuation_date[:10])
            delta = (quote_date - day(shares["as_of"])).days
            # Share-count recency protects against using an outdated or future denominator.
            if 0 <= delta <= 135 and day(shares["filed_on"]) <= quote_date:
                cap = price * shares["value"]
                share_date = shares["as_of"]
                cap_source = "approximation: observed price times SEC cover-page common shares, subject to corporate actions and share classes"
    latest_balance = next((row for row in (financials or {}).get("quarterly", []) if row["metrics"].get("equity") is not None), None)
    if latest_balance is None:
        latest_balance = next((row for row in (financials or {}).get("annual", []) if row["metrics"].get("equity") is not None), None)
    book = latest_balance["metrics"]["equity"] if latest_balance else None
    market_date = valuation_date[:10] if valuation_date else None
    same_currency = cap is not None and cap_currency == currency
    known_ttm = same_currency and amounts and market_date and amounts.get("filed_on") and amounts["filed_on"] <= market_date
    known_book = (same_currency and latest_balance and market_date and latest_balance.get("filed_on") and
                  latest_balance["filed_on"] <= market_date)
    reported = {field: number(yahoo_info.get(provider)) for field, provider in {
        "pe_ttm": "trailingPE", "forward_pe": "forwardPE", "pb": "priceToBook", "ps_ttm": "priceToSalesTrailing12Months",
        "peg_5y_expected": "trailingPegRatio"}.items()}
    computed = {"pe_ttm": ratio(cap, values.get("net_income")) if known_ttm else None,
                "pb": ratio(cap, book) if known_book else None,
                "ps_ttm": ratio(cap, values.get("revenue")) if known_ttm else None}
    if cap is None and not any(value is not None for value in reported.values()):
        return None
    return {"market_cap": cap, "market_cap_method": cap_source, "market_currency": cap_currency,
            "market_data_as_of": valuation_date, "share_count_as_of": share_date, "computed": computed,
            "ttm_period_end": amounts["end"] if known_ttm else None,
            "book_period_end": latest_balance["end"] if known_book else None,
            "yahoo_reported": reported if any(value is not None for value in reported.values()) else None,
            "yahoo_reported_basis": "provider ratios with separate fiscal methodology and unspecified ratio timestamp"
                                    if any(value is not None for value in reported.values()) else None}


def _expectations(info):
    result = {name: number(info.get(name)) for name in ["targetMeanPrice", "targetLowPrice", "targetHighPrice",
                                                       "numberOfAnalystOpinions", "forwardEps"]}
    result["recommendation"] = info.get("recommendationKey")
    if not any(value is not None for value in result.values()):
        return None
    result["provider"] = "Yahoo Finance"
    result["target_currency"] = info.get("currency")
    result["classification"] = "provider consensus estimates"
    return result


def _nasdaq_expectations(ticker):
    url = NASDAQ_EPS_FORECAST.format(symbol=quote(ticker, safe=""))
    data = get_json(url).get("data") or {}
    if data.get("symbol", "").upper() != ticker:
        raise ProviderError("Nasdaq returned no matching analyst forecast")
    series = {}
    for label, key in (("quarterly_eps", "quarterlyForecast"), ("annual_eps", "yearlyForecast")):
        table = data.get(key) or {}
        items = []
        for row in table.get("rows", []) or []:
            consensus = number(row.get("consensusEPSForecast"))
            if consensus is not None and row.get("fiscalEnd"):
                items.append({"fiscal_period_end_label": row["fiscalEnd"], "consensus": consensus,
                              "high": number(row.get("highEPSForecast")), "low": number(row.get("lowEPSForecast")),
                              "analysts": number(row.get("noOfEstimates")), "up_revisions_4w": number(row.get("up")),
                              "down_revisions_4w": number(row.get("down"))})
        if items:
            series[label] = {"provider_as_of": table.get("asOf"), "items": items[:5]}
    if not series:
        raise ProviderError("Nasdaq returned no dated EPS consensus rows")
    return {"provider": "Nasdaq", "classification": "provider consensus estimates", **series}, url


def _yahoo_financial_summary(info):
    currency = info.get("financialCurrency")
    amounts = {field: number(info.get(provider)) for field, provider in {
        "revenue_ttm": "totalRevenue", "net_income_ttm_common": "netIncomeToCommon", "gross_profit_ttm": "grossProfits",
        "cash_mrq": "totalCash", "debt_mrq": "totalDebt", "operating_cash_flow_ttm": "operatingCashflow",
        "free_cash_flow_ttm": "freeCashflow", "gross_margin": "grossMargins", "net_margin": "profitMargins",
        "roe": "returnOnEquity", "roa": "returnOnAssets", "revenue_growth_latest_quarter_yoy": "revenueGrowth",
        "earnings_growth_latest_quarter_yoy": "earningsGrowth"}.items()}
    if not isinstance(currency, str) or not re.fullmatch(r"[A-Z]{3}", currency):
        return None
    return {"provider": "Yahoo Finance", "currency": currency, "summary": amounts, "annual": [], "quarterly": [],
            "ttm": None, "units": {"statement_amounts": currency, "margins_and_growth": "decimal ratio"},
            "note": "provider-supplied summary; fiscal dates and methodology may differ from SEC XBRL"}


def _yahoo_statement_rows(ticker, freq, cutoff):
    periods = defaultdict(dict)
    failures = []
    for method, columns in YAHOO_STATEMENTS.items():
        try:
            data = getattr(ticker, method)(freq=freq, pretty=False)
        except Exception as exc:
            failures.append(f"{method}: {type(exc).__name__}")
            continue
        if data is None or not hasattr(data, "columns"):
            continue
        for period in data.columns:
            try:
                end = period.date()
            except (AttributeError, ValueError):
                continue
            if type(end) is not date or end > cutoff:
                continue
            for field, aliases in columns.items():
                for alias in aliases:
                    val = number(data.at[alias, period]) if alias in data.index else None
                    if val is None:
                        continue
                    metadata = {"value": val, "concept": alias, "filed_on": None, "provider": "Yahoo Finance"}
                    if field == "capex":
                        metadata["value"] = -val
                        metadata["method"] = "negate Yahoo's signed cash outflow to express capex as a deduction"
                    periods[end][field] = metadata
                    break
    rows = []
    for period_end, facts in sorted(periods.items(), reverse=True):
        cadence = "annual" if freq == "yearly" else "interim_provider_frequency"
        row = {"start": None, "end": period_end.isoformat(), "cadence": cadence,
               "provider_requested_frequency": freq, "facts": facts}
        _calculate_row(row)
        rows.append(row)
    for row in rows:
        current = row["metrics"]
        comparable = next((previous["metrics"] for previous in rows
                           if abs((day(row["end"]) - day(previous["end"])).days - 365) <= 14), {})
        for field in ("revenue", "net_income"):
            growth = ratio(current.get(field), comparable.get(field))
            current[f"{field}_yoy"] = growth - 1 if growth is not None else None
        if freq == "yearly":
            for name, field in (("roe", "equity"), ("roa", "assets")):
                opening, closing = comparable.get(field), current.get(field)
                average = (opening + closing) / 2 if opening is not None and closing is not None else None
                current[name] = ratio(current.get("net_income"), average)
    return rows[:8], failures


def _yahoo_financials(symbol, info, cutoff, errors):
    result = _yahoo_financial_summary(info)
    if result is None:
        return None
    import yfinance as yf
    ticker = yf.Ticker(symbol)
    for name, freq in (("annual", "yearly"), ("quarterly", "quarterly")):
        result[name], failures = _yahoo_statement_rows(ticker, freq, cutoff)
        if failures:
            errors.append({"provider": f"Yahoo Finance {name} statements", "message": ", ".join(failures)})
    if not (result["annual"] or result["quarterly"] or any(val is not None for val in result["summary"].values())):
        return None
    result["note"] = ("Latest Yahoo snapshots include fiscal period end dates where available. The provider does not expose "
                      "filing dates, so these statements are current-use only and no point-in-time TTM is calculated. "
                      "The quarterly request can include interim snapshots of unspecified duration.")
    return result


def fundamentals(symbol, user_agent=None, *, yahoo=True, as_of=None, market_cap=None, market_currency=None,
                 market_price=None, price_as_of=None):
    """Return filing-based financials and optional current provider consensus."""
    today = utc_now().date()
    cutoff = day(as_of) if as_of else today
    if cutoff > today:
        raise ValueError("fundamentals as_of cannot be in the future")
    symbol = symbol.strip().upper().removesuffix(".US")
    if any(ch in symbol for ch in "/\\?#&") or not symbol:
        raise ValueError("invalid fundamentals ticker")
    sources, errors = [], []
    profile_sec, facts_raw, yahoo_info = {}, {}, {}
    financials = None
    try:
        cik = get_cik(symbol, user_agent)
        if not cik and sec_ticker(symbol):
            errors.append({"provider": "SEC EDGAR", "message": "ticker not listed in the SEC mapping"})
    except ProviderError as exc:
        cik = None
        errors.append({"provider": "SEC EDGAR ticker lookup", "message": str(exc)})
    if cik:
        try:
            facts_raw = fetch_facts(cik, sec_contact(user_agent))
            financials = _parse_sec_facts(facts_raw, cutoff)
            financials["cik"] = cik
            financials["source_url"] = SEC_FACTS.format(cik=cik)
            sources.append({"provider": "SEC EDGAR companyfacts", "url": financials["source_url"]})
        except ProviderError as exc:
            errors.append({"provider": "SEC EDGAR companyfacts", "message": str(exc)})
        if cutoff == today:
            try:
                profile_sec = fetch_submissions(cik, sec_contact(user_agent))
                sources.append({"provider": "SEC EDGAR submissions", "url": SEC_SUBMISSIONS.format(cik=cik)})
            except ProviderError as exc:
                errors.append({"provider": "SEC EDGAR submissions", "message": str(exc)})
    if yahoo and cutoff == today:
        try:
            yahoo_info = _yahoo_data(symbol)
            sources.append({"provider": "Yahoo Finance", "url": f"https://finance.yahoo.com/quote/{quote(symbol, safe='')}/"})
        except ProviderError as exc:
            errors.append({"provider": "Yahoo Finance", "message": str(exc)})
    if not financials:
        financials = _yahoo_financials(symbol, yahoo_info, cutoff, errors)
    # Do not approximate issuer capitalization using one listing's share price
    # when submissions identify multiple listed classes or depositary shares.
    listing = profile_sec.get("tickers", [])
    single_listing = listing == [sec_ticker(symbol)] and profile_sec.get("entityType") == "operating"
    valuation = _valuation(financials, yahoo_info, market_cap, market_currency, market_price, price_as_of, single_listing)
    expectations = _expectations(yahoo_info)
    if yahoo and cutoff == today and not expectations and sec_ticker(symbol):
        try:
            expectations, source_url = _nasdaq_expectations(sec_ticker(symbol))
            sources.append({"provider": "Nasdaq analyst EPS forecast", "url": source_url})
        except ProviderError as exc:
            errors.append({"provider": "Nasdaq analyst EPS forecast", "message": str(exc)})
    profile = _profile(profile_sec, facts_raw, yahoo_info) if cutoff == today else None
    available = any((financials, valuation, expectations, profile))
    return {"status": ("ok" if financials and not errors else "partial") if available else "unavailable",
            "symbol": symbol, "as_of": cutoff.isoformat(), "retrieved_at": utc_now().isoformat(), "sources": sources,
            "errors": errors, "data": {"profile": profile, "financials": financials, "valuation": valuation,
                                        "analyst_expectations": expectations}}


def compare_peers(symbols, user_agent=None, *, yahoo=True, as_of=None):
    """Compare explicit peers with ratios kept alongside basis and dates."""
    if isinstance(symbols, str):
        raise ValueError("peers must be a list of ticker strings")
    symbols = list(dict.fromkeys(symbol.strip().upper() for symbol in symbols if symbol.strip()))
    if not 1 <= len(symbols) <= 4:
        raise ValueError("peer comparison accepts one to four distinct tickers")
    result = []
    for symbol in symbols:
        peer = fundamentals(symbol, user_agent, yahoo=yahoo, as_of=as_of)
        details = peer["data"]
        financial = details["financials"] or {}
        period = financial.get("ttm") or next(iter(financial.get("annual", [])), {})
        values = period.get("metrics", {})
        result.append({"symbol": symbol, "status": peer["status"], "profile": details["profile"],
                       "period_end": period.get("end"), "period_basis": period.get("cadence"),
                       "currency": financial.get("currency"),
                       "revenue": values.get("revenue"), "net_margin": values.get("net_margin"),
                       "revenue_yoy": values.get("revenue_yoy"), "roe": values.get("roe"),
                       "valuation": details["valuation"], "errors": peer["errors"], "sources": peer["sources"]})
    return result
