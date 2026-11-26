"""Approximate prices/filing-based historical valuation snapshots for SEC issuers."""
from __future__ import annotations

from datetime import date, timedelta

from analytics import completed, finite
from fundamentals import ProviderError, _parse_sec_facts, fetch_facts, fetch_submissions, ratio, sec_contact


def _filing_dates(submissions):
    recent = submissions.get("filings", {}).get("recent", {})
    return sorted({filed for form, filed in zip(recent.get("form", []), recent.get("filingDate", []))
                   if form in {"10-K", "10-Q"} and filed}, reverse=True)


def _snapshot(bar, history_start, splits, raw_facts):
    session = bar["date"]
    price = bar.get("raw_close")
    if not finite(price) or price <= 0:
        return None
    # Exclude same-day facts, which may have been filed after the close.
    try:
        cutoff = date.fromisoformat(session) - timedelta(days=1)
        accounting = _parse_sec_facts(raw_facts, cutoff, first_filed=True)
    except ProviderError:
        return None
    latest = accounting.get("ttm") or {}
    balance = next((row for row in accounting.get("quarterly", []) + accounting.get("annual", [])
                    if row["filed_on"] < session and row["metrics"].get("equity") is not None), {})
    if accounting.get("currency") != "USD":
        return None
    shares = accounting.get("shares_outstanding_cover_page") or {}
    share_date = shares.get("as_of")
    share_count = shares.get("value")
    if (not share_date or not finite(share_count) or share_count <= 0 or
            not shares.get("filed_on") or shares["filed_on"] >= session or share_date < history_start
            or not 0 <= (date.fromisoformat(session) - date.fromisoformat(share_date)).days <= 135
            or any(item["date"] > share_date for item in splits)):
        return None
    cap = price * share_count
    revenue = latest.get("facts", {}).get("revenue") or {}
    earnings = latest.get("facts", {}).get("net_income") or {}
    values = {"pe_ttm": ratio(cap, earnings["value"]) if earnings.get("filed_on", session) < session else None,
              "pb": ratio(cap, balance.get("metrics", {}).get("equity")),
              "ps_ttm": ratio(cap, revenue["value"]) if revenue.get("filed_on", session) < session else None}
    if not any(value is not None for value in values.values()):
        return None
    return {"session": session, "price": price, "market_cap_estimate": cap, "currency": "USD",
            "shares_as_of": share_date, "shares_reported_on": shares["filed_on"],
            "ttm_period_end": latest.get("end"), "book_period_end": balance.get("end"), **values}


def historical_valuation(financials, history, *, requested_as_of=None):
    """Pair the first completed close after a 10-Q/10-K with published inputs."""
    unavailable = {"status": "unavailable", "observations": []}
    if requested_as_of:
        return dict(unavailable, reason="latest corporate-action coverage is required for historical valuations")
    cik = (financials or {}).get("cik")
    if not cik or not history or history.get("interval") != "1d" or history.get("currency") != "USD":
        return dict(unavailable, reason="requires SEC financials and USD daily OHLC")
    splits = history.get("splits")
    if history.get("adjustment") not in {"split_only", "split_dividend"} or not isinstance(splits, list):
        return dict(unavailable, reason="provider does not establish the close basis and split-event coverage")
    bars = completed(history)
    if not bars:
        return dict(unavailable, reason="no completed daily bars")
    contact = sec_contact(None)
    try:
        filings = _filing_dates(fetch_submissions(cik, contact))
        facts = fetch_facts(cik, contact)
    except ProviderError as exc:
        return dict(unavailable, reason=str(exc))
    observations = []
    for filing in filings:
        if len(observations) == 6 or filing < bars[0]["date"]:
            break
        bar = next((row for row in bars if row["date"] > filing), None)
        if bar is None or (date.fromisoformat(bar["date"]) - date.fromisoformat(filing)).days > 7:
            continue
        point = _snapshot(bar, bars[0]["date"], splits, facts)
        if point:
            observations.append(dict(point, filing_date=filing))
    return {"status": "ok" if observations else "unavailable", "observations": observations,
            "reason": None if observations else "insufficient matched filing, split or share-count coverage",
            "method": "first completed close after 10-Q/10-K, times recently disclosed common shares; "
                      "uses SEC first-filed facts and skips dates with a subsequent observed split",
            "sources": [history.get("source"),
                        {"provider": "SEC EDGAR companyfacts", "url": financials.get("source_url")},
                        {"provider": "SEC EDGAR submissions", "url": f"https://data.sec.gov/submissions/CIK{cik}.json"}]}
