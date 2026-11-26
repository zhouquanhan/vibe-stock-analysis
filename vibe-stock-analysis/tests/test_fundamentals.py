import json
import unittest
from datetime import date, datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

import fundamentals as f


def fact(start, end, filed, value, form="10-Q"):
    result = {"end": end, "filed": filed, "val": value, "form": form, "accn": "0001045810-25-000001"}
    if start:
        result["start"] = start
    return result


def units(rows, unit="USD"):
    return {"units": {unit: rows}}


def companyfacts():
    periods = [("2024-01-01", "2024-03-31", "2024-05-01", 15),
               ("2024-04-01", "2024-06-30", "2024-08-01", 20),
               ("2024-07-01", "2024-09-30", "2024-11-01", 25)]
    reported = [fact(*period) for period in periods] + [fact("2024-01-01", "2024-12-31", "2025-02-15", 100, "10-K")]
    reported += [fact("2024-04-01", "2024-06-30", "2025-05-01", 21)]
    fiscal_ends = [("2024-03-31", "2024-05-01", 10), ("2024-06-30", "2024-08-01", 30),
                   ("2024-09-30", "2024-11-01", 55), ("2024-12-31", "2025-02-15", 100)]
    cash = [fact("2024-01-01", end, filed, value, "10-K" if end.endswith("12-31") else "10-Q")
            for end, filed, value in fiscal_ends]
    balances = [fact(None, "2023-12-31", "2024-02-15", 80, "10-K"),
                fact(None, "2024-12-31", "2025-02-15", 120, "10-K")]
    concepts = {"Revenues": units(reported), "NetIncomeLoss": units([dict(item, val=item["val"] * 0.2) for item in reported]),
                "NetCashProvidedByUsedInOperatingActivities": units(cash), "Assets": units(balances),
                "StockholdersEquity": units([dict(item, val=item["val"] * 0.5) for item in balances]),
                "GrossProfit": units([dict(item, val=item["val"] * 0.4) for item in reported])}
    shares = [fact(None, "2025-02-12", "2025-02-15", 50, "10-K")]
    return {"entityName": "Fixture Corporation", "facts": {
        "us-gaap": concepts, "dei": {"EntityCommonStockSharesOutstanding": units(shares, "shares")}}}


class FundamentalsTest(unittest.TestCase):
    def test_year_to_date_difference_and_annual_returns(self):
        result = f._parse_sec_facts(companyfacts(), date(2025, 3, 1))
        q4 = result["quarterly"][0]
        self.assertEqual((q4["start"], q4["end"]), ("2024-10-01", "2024-12-31"))
        self.assertEqual(q4["metrics"]["operating_cash_flow"], 45)
        self.assertEqual(q4["metrics"]["revenue"], 40)
        self.assertEqual(result["ttm"]["metrics"]["revenue"], 100)
        self.assertAlmostEqual(result["ttm"]["metrics"]["roe"], .4)
        self.assertAlmostEqual(result["annual"][0]["metrics"]["roa"], .2)
        self.assertAlmostEqual(result["annual"][0]["metrics"]["gross_margin"], .4)

    def test_revision_is_not_visible_before_filing(self):
        before = f._parse_sec_facts(companyfacts(), date(2025, 3, 1))["quarterly"][2]
        after = f._parse_sec_facts(companyfacts(), date(2025, 5, 2))["quarterly"][2]
        self.assertEqual(before["metrics"]["revenue"], 20)
        self.assertEqual(after["metrics"]["revenue"], 21)
        self.assertEqual(after["facts"]["revenue"]["first_filed_on"], "2024-08-01")
        self.assertEqual(after["facts"]["revenue"]["filed_on"], "2025-05-01")

    def test_market_cap_without_timestamp_does_not_drive_computed_multiples(self):
        financials = f._parse_sec_facts(companyfacts(), date(2025, 3, 1))
        info = {"marketCap": 500, "currency": "USD", "trailingPE": 21.0}
        result = f._valuation(financials, info, None, None, None, None, True)
        self.assertIsNone(result["market_data_as_of"])
        self.assertIsNone(result["computed"]["pe_ttm"])
        self.assertEqual(result["yahoo_reported"]["pe_ttm"], 21.0)

    def test_market_cap_applies_quote_basis_and_decimal_currency(self):
        financials = f._parse_sec_facts(companyfacts(), date(2025, 3, 1))
        result = f._valuation(financials, {}, None, "USD", 10, "2025-03-01T20:30:00+00:00", True)
        self.assertEqual(result["market_cap"], 500)
        self.assertEqual(result["computed"]["ps_ttm"], 5)
        self.assertAlmostEqual(result["computed"]["pe_ttm"], 25)
        self.assertIsNone(f._valuation(financials, {}, None, "USD", 10, "2025-03-01", False))
        mismatched = f._valuation(financials, {}, 500, "HKD", None, "2025-03-01", True)
        self.assertIsNone(mismatched["computed"]["ps_ttm"])

    def test_sec_exact_ticker_fallback_rejects_fuzzy_match(self):
        f._ticker_table.cache_clear()
        f._ticker_search.cache_clear()
        search = {"hits": {"hits": [{"_id": "111", "_source": {"tickers": "FAKE, EXACTA"}},
                                    {"_id": "345", "_source": {"tickers": "EXACT, THIRD"}}]}}
        with patch.object(f, "get_json", side_effect=[f.ProviderError("HTTP 403"), search]):
            self.assertEqual(f.get_cik("EXACT"), "0000000345")
        f._ticker_search.cache_clear()

    def test_public_filing_data_survive_yahoo_error(self):
        fixed = datetime(2025, 3, 1, tzinfo=timezone.utc)
        profile = {"name": "Fixture Corporation", "tickers": ["NVDA"], "entityType": "operating"}
        with patch.object(f, "utc_now", return_value=fixed), patch.object(f, "fetch_facts", return_value=companyfacts()), \
                patch.object(f, "fetch_submissions", return_value=profile), \
                patch.object(f, "_yahoo_data", side_effect=f.ProviderError("HTTP 429")), \
                patch.object(f, "_nasdaq_expectations", return_value=({"provider": "Nasdaq"}, "https://api.nasdaq.com/")):
            result = f.fundamentals("NVDA", market_currency="USD", market_price=10, price_as_of="2025-03-01")
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["data"]["financials"]["ttm"]["metrics"]["revenue"], 100)
        self.assertEqual(result["data"]["valuation"]["market_cap"], 500)
        self.assertEqual(result["data"]["analyst_expectations"]["provider"], "Nasdaq")
        self.assertEqual(result["errors"], [{"provider": "Yahoo Finance", "message": "HTTP 429"}])
        json.dumps(result, allow_nan=False)

    def test_yahoo_statements_keep_unknown_pit_dates_and_explicit_capex_sign(self):
        earlier, latest = datetime(2023, 12, 31), datetime(2024, 12, 31)

        def table(values):
            return SimpleNamespace(columns=[latest, earlier], index=values,
                                   at={(field, period): value for field, series in values.items()
                                       for period, value in zip([latest, earlier], series)})

        ticker = SimpleNamespace(
            get_income_stmt=lambda **_: table({"TotalRevenue": [100, 80], "NetIncome": [20, 16], "GrossProfit": [40, 28]}),
            get_balance_sheet=lambda **_: table({"TotalAssets": [200, 160], "StockholdersEquity": [80, 60]}),
            get_cash_flow=lambda **_: table({"OperatingCashFlow": [35, 30], "CapitalExpenditure": [-15, -12]}),
        )
        rows, errors = f._yahoo_statement_rows(ticker, "yearly", date(2025, 3, 1))
        self.assertEqual(errors, [])
        self.assertIsNone(rows[0]["start"])
        self.assertIsNone(rows[0]["filed_on"])
        self.assertEqual(rows[0]["metrics"]["free_cash_flow"], 20)
        self.assertAlmostEqual(rows[0]["metrics"]["revenue_yoy"], .25)
        self.assertAlmostEqual(rows[0]["metrics"]["roe"], 20 / 70)


class TransportTest(unittest.TestCase):
    def test_system_curl_cert_fallback_uses_verified_https_and_http_one(self):
        import subprocess

        from fundamentals import _curl_bytes

        response = subprocess.CompletedProcess([], 0, b"data", b"")
        with patch("fundamentals.shutil.which", return_value="curl"), \
             patch("fundamentals.subprocess.run", return_value=response) as run:
            self.assertEqual(_curl_bytes("https://fred.stlouisfed.org/", {"Accept": "text/csv"}), b"data")
        command = run.call_args.args[0]
        self.assertIn("--http1.1", command)
        self.assertIn("=https", command)
        self.assertNotIn("--insecure", command)


if __name__ == "__main__":
    unittest.main()
