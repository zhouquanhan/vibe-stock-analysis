"""Read-only filings, financial headlines and macroeconomic context.

SEC filing projection, Eastmoney/Yahoo headline routing and FRED collection are
adapted from Vibe-Trading, Copyright (c) 2026 Vibe-Trading Contributors (MIT).
See LICENSE and references/upstream.md in the distribution.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import csv
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html import unescape
from io import StringIO
import json
import re
from urllib.parse import quote, urlencode, urlsplit
from xml.etree import ElementTree

from fundamentals import (ProviderError, SEC_SUBMISSIONS, day, fetch_submissions, filing_url, get_bytes,
                          get_cik, get_json, number, sec_contact, sec_ticker, utc_now)

YAHOO_SEARCH = "https://query1.finance.yahoo.com/v1/finance/search"
GOOGLE_RSS = "https://news.google.com/rss/search"
EASTMONEY_NEWS = "https://search-api-web.eastmoney.com/search/jsonp"
FRED_CSV = "https://fred.stlouisfed.org/graph/fredgraph.csv"
RELEVANT_FORMS = frozenset({"10-K", "10-Q", "8-K", "20-F", "40-F", "6-K"})
MACRO_SERIES = {
    "DFF": {"label": "Effective Federal Funds Rate", "unit": "percent", "frequency": "daily"},
    "DGS10": {"label": "10-Year Treasury Constant Maturity Rate", "unit": "percent", "frequency": "daily"},
    "CPIAUCSL": {"label": "U.S. CPI: All Items", "unit": "index 1982–1984=100, seasonally adjusted", "frequency": "monthly"},
}


def _plain(text):
    return " ".join(unescape(re.sub(r"<[^>]+>", "", text)).split()) if isinstance(text, str) else ""


def _link(value):
    if not isinstance(value, str):
        return None
    try:
        parsed = urlsplit(value)
    except ValueError:
        return None
    return value if parsed.scheme in {"https", "http"} and parsed.hostname and not parsed.username else None


def _article(title, published, publisher, url, catalog, snippet=None):
    title = _plain(title)
    url = _link(url)
    if not title or not url or not published:
        return None
    return {"title": title[:300], "published": published, "publisher": _plain(publisher)[:100] or None,
            "url": url, "catalog": catalog, "snippet": _plain(snippet)[:280] or None}


def _deduplicate(rows, cutoff, limit):
    result = []
    seen = set()
    for article in sorted((item for item in rows if item), key=lambda article: article["published"], reverse=True):
        date_label = article["published"][:10]
        if day(date_label) > cutoff or day(date_label) < cutoff - timedelta(days=45):
            continue
        title = article["title"].casefold()
        if title not in seen:
            seen.add(title)
            result.append(article)
    return result[:limit]


def _yahoo_news(symbol, company_name, cutoff, limit):
    url = f"{YAHOO_SEARCH}?{urlencode({'q': symbol, 'quotesCount': 0, 'newsCount': max(16, limit)})}"
    payload = get_json(url)
    rows = []
    for row in payload.get("news", []):
        stamp = number(row.get("providerPublishTime"))
        try:
            published = datetime.fromtimestamp(stamp, tz=timezone.utc).isoformat() if stamp is not None else None
        except (ValueError, OverflowError, OSError):
            published = None
        item = _article(row.get("title"), published, row.get("publisher"), row.get("link"), "Yahoo Finance",
                        row.get("summary"))
        if item:
            bare_name = re.split(r"[,\s]", company_name or "", maxsplit=1)[0].casefold()
            names = [name for name in (symbol.casefold(), bare_name) if len(name) > 2]
            mentions = any(re.search(r"\b" + re.escape(name) + r"\b", item["title"].casefold()) for name in names)
            tagged = symbol in (row.get("relatedTickers") or [])
            item["relevance"] = ("company_or_ticker_in_title" if mentions else
                                 "provider_ticker_tag" if tagged else "broader_market_or_unverified_match")
            rows.append(item)
    unique = _deduplicate(rows, cutoff, max(16, limit))
    order = {"company_or_ticker_in_title": 0, "provider_ticker_tag": 1, "broader_market_or_unverified_match": 2}
    ranked = sorted(unique, key=lambda item: order[item["relevance"]])
    return sorted(ranked[:limit], key=lambda item: item["published"], reverse=True), url


def _google_news(symbol, company_name, cutoff, limit):
    company = f'"{company_name}" ' if company_name else ""
    base_query = f"{company}{symbol} stock when:45d"
    url = f"{GOOGLE_RSS}?{urlencode({'q': base_query, 'hl': 'en-US', 'gl': 'US', 'ceid': 'US:en'})}"
    try:
        tree = ElementTree.fromstring(get_bytes(url, accept="application/rss+xml,application/xml,text/xml"))
    except ElementTree.ParseError as exc:
        raise ProviderError("Google News returned an invalid RSS document") from exc
    rows = []
    for row in tree.findall("./channel/item"):
        try:
            parsed = parsedate_to_datetime(row.findtext("pubDate", ""))
            if parsed.tzinfo is None:
                continue
            published = parsed.astimezone(timezone.utc).isoformat()
        except (ValueError, TypeError, OverflowError):
            continue
        rows.append(_article(row.findtext("title"), published, row.findtext("source"), row.findtext("link"),
                             "Google News RSS"))
    return _deduplicate(rows, cutoff, limit), url


def _eastmoney_news(symbol, company_name, cutoff, limit):
    settings = {"uid": "", "keyword": company_name or symbol.split(".", 1)[0], "type": ["cmsArticleWebOld"],
                "client": "web", "clientType": "web", "param": {"cmsArticleWebOld": {
                    "searchScope": "default", "sort": "default", "pageIndex": 1, "pageSize": limit}}}
    url = f"{EASTMONEY_NEWS}?{urlencode({'cb': '', 'param': json.dumps(settings, ensure_ascii=False), '_': '0'})}"
    raw = get_bytes(url).decode("utf-8", errors="replace").strip()
    if raw.endswith((")", ");")) and not raw.startswith("{"):
        start = raw.find("(")
        if start >= 0:
            raw = raw[start + 1:raw.rfind(")")]
    try:
        payload = json.loads(raw)
    except ValueError as exc:
        raise ProviderError("Eastmoney news returned invalid JSON") from exc
    rows = []
    for row in (payload.get("result") or {}).get("cmsArticleWebOld", []):
        try:
            stamp = datetime.fromisoformat(str(row.get("date")))
            if stamp.tzinfo is None:
                stamp = stamp.replace(tzinfo=timezone(timedelta(hours=8)))
            published = stamp.isoformat()
        except ValueError:
            continue
        rows.append(_article(row.get("title"), published, row.get("mediaName"), row.get("url"), "Eastmoney", row.get("content")))
    return _deduplicate(rows, cutoff, limit), url


def _recent_filings(cik, profile, cutoff, limit):
    arrays = profile.get("filings", {}).get("recent", {})
    forms = arrays.get("form", [])
    rows = []
    for i, form in enumerate(forms):
        if form.removesuffix("/A") not in RELEVANT_FORMS:
            continue
        values = {field: array[i] if i < len(array) else None for field, array in arrays.items() if isinstance(array, list)}
        filing_date = values.get("filingDate")
        if not filing_date or filing_date > cutoff.isoformat():
            continue
        accession = values.get("accessionNumber")
        url = filing_url(cik, accession, values.get("primaryDocument"))
        index_url = filing_url(cik, accession)
        items = values.get("items") or None
        rows.append({"form": form, "filed_on": filing_date, "accepted_at": values.get("acceptanceDateTime"),
                     "report_date": values.get("reportDate") or None, "accession": accession, "url": url,
                     "filing_index_url": index_url, "items": items, "description": values.get("primaryDocDescription"),
                     "earnings_release_candidate": bool(items and "2.02" in items)})
    # Include both the latest annual and latest interim filing even when event
    # filings fill a short output limit. Event text and exhibits are not parsed.
    rows.sort(key=lambda row: (row["filed_on"], row["accepted_at"] or ""), reverse=True)
    important = []
    for family in ({"10-K", "20-F", "40-F"}, {"10-Q"}):
        row = next((row for row in rows if row["form"].removesuffix("/A") in family), None)
        if row:
            important.append(row)
    for row in rows:
        if len(important) >= limit:
            break
        if row not in important:
            important.append(row)
    return sorted(important[:limit], key=lambda row: (row["filed_on"], row["accepted_at"] or ""), reverse=True)


def _fred(series, cutoff):
    lookback_days = 800 if series == "CPIAUCSL" else 60
    start = (cutoff - timedelta(days=lookback_days)).isoformat()
    query = urlencode({"id": series, "cosd": start, "coed": cutoff.isoformat()})
    url = f"{FRED_CSV}?{query}"
    raw = get_bytes(url, user_agent="", accept="text/csv").decode("utf-8-sig")
    observations = []
    for row in csv.DictReader(StringIO(raw)):
        value = number(row.get(series))
        observed = row.get("observation_date") or row.get("DATE")
        if value is not None and observed and re.fullmatch(r"\d{4}-\d{2}-\d{2}", observed) and observed <= cutoff.isoformat():
            observations.append({"observation_date": observed, "value": value})
    if not observations:
        raise ProviderError("FRED returned no usable observations")
    observations.sort(key=lambda row: row["observation_date"])
    result = {"series": series, **MACRO_SERIES[series], "latest": observations[-1],
              "previous": observations[-2] if len(observations) > 1 else None,
              "source_url": f"https://fred.stlouisfed.org/series/{quote(series)}",
              "data_url": url, "vintage": "latest available revision; observation date can precede release date"}
    if series == "CPIAUCSL":
        latest = result["latest"]
        by_day = {row["observation_date"]: row for row in observations}

        def year_ago(row):
            observed_date = day(row["observation_date"])
            return by_day.get(observed_date.replace(year=observed_date.year - 1, day=1).isoformat())

        prior = year_ago(latest)
        result["year_ago"] = prior
        result["year_over_year_pct"] = 100 * (latest["value"] / prior["value"] - 1) if prior and prior["value"] > 0 else None
        for row in reversed(observations[-6:]):
            previous = year_ago(row)
            if previous and previous["value"] > 0:
                result["latest_comparable_year_over_year"] = {
                    "observation_date": row["observation_date"], "year_ago_observation_date": previous["observation_date"],
                    "pct": 100 * (row["value"] / previous["value"] - 1)}
                break
    return result


def context(symbol, user_agent=None, *, news=True, macro=True, as_of=None, limit=8, macro_scope="auto", company_name=None):
    """Return recent filing metadata, attributed headlines and optional U.S. macro."""
    today = utc_now().date()
    cutoff = day(as_of) if as_of else today
    if cutoff > today:
        raise ValueError("context as_of cannot be in the future")
    if not 2 <= limit <= 20:
        raise ValueError("context limit must be between 2 and 20")
    if macro_scope not in {"auto", "US"}:
        raise ValueError("macro_scope must be auto or US")
    symbol = symbol.strip().upper().removesuffix(".US")
    filings, news_items, macro_data, sources, errors = [], [], [], [], []
    if not symbol or any(ch in symbol for ch in "/\\?#&"):
        raise ValueError("invalid context ticker")
    try:
        cik = get_cik(symbol, user_agent)
        if cik:
            profile = fetch_submissions(cik, sec_contact(user_agent))
            company_name = company_name or profile.get("name")
            filings = _recent_filings(cik, profile, cutoff, limit)
            sources.append({"provider": "SEC EDGAR submissions", "url": SEC_SUBMISSIONS.format(cik=cik)})
    except ProviderError as exc:
        errors.append({"provider": "SEC EDGAR submissions", "message": str(exc)})
    if news and (today - cutoff).days <= 14:
        providers = [("Yahoo Finance headlines", _yahoo_news), ("Google News RSS", _google_news)]
        if symbol.endswith((".SS", ".SH", ".SZ", ".BJ")):
            providers.insert(0, ("Eastmoney headlines", _eastmoney_news))
        for name, provider in providers:
            try:
                news_items, url = provider(symbol, company_name, cutoff, limit)
                if news_items:
                    sources.append({"provider": name, "url": url})
                    break
                errors.append({"provider": name, "message": "no dated headlines available for this symbol and cutoff"})
            except ProviderError as exc:
                errors.append({"provider": name, "message": str(exc)})
    elif news:
        errors.append({"provider": "recent headlines", "message": "archival article search is unavailable"})
    is_us_scope = bool(sec_ticker(symbol)) or macro_scope == "US"
    if macro and is_us_scope and cutoff == today:
        with ThreadPoolExecutor(max_workers=len(MACRO_SERIES)) as pool:
            jobs = [(series, pool.submit(_fred, series, cutoff)) for series in MACRO_SERIES]
            for series, job in jobs:
                try:
                    result = job.result()
                    macro_data.append(result)
                    sources.append({"provider": f"FRED {series}", "url": result["source_url"]})
                except (ProviderError, UnicodeError, csv.Error) as exc:
                    errors.append({"provider": f"FRED {series}", "message": str(exc)})
    elif macro and is_us_scope:
        errors.append({"provider": "FRED", "message": "point-in-time macro data vintages are unavailable"})
    elif macro:
        errors.append({"provider": "macro", "message": "no automatic local macro series for this listing; US scope can be requested"})
    available = any((filings, news_items, macro_data))
    return {"status": ("partial" if errors else "ok") if available else "unavailable", "symbol": symbol,
            "as_of": cutoff.isoformat(), "retrieved_at": utc_now().isoformat(), "sources": sources, "errors": errors,
            "data": {"filings": filings, "news": news_items, "macro": macro_data,
                     "market_scope": "US macro reference" if is_us_scope else None,
                     "coverage_notes": ["Headlines are dated publisher metadata. Verify full text and issuer guidance in primary sources.",
                                        "Use release vintages for historical macro analysis; current FRED series can be revised."]}}
