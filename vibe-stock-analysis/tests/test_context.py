import unittest
from datetime import date, datetime, timezone
from unittest.mock import patch

import context as c
from fundamentals import ProviderError


class ContextTest(unittest.TestCase):
    def test_headline_failover_and_filing_cutoff(self):
        now = datetime(2025, 3, 1, tzinfo=timezone.utc)
        article = {"title": "A dated headline", "published": "2025-02-27T17:00:00+00:00", "publisher": "Example"}
        filings = {"name": "Fixture Corp", "filings": {"recent": {
            "form": ["8-K", "8-K", "10-K"], "filingDate": ["2025-03-02", "2025-02-26", "2025-02-15"],
            "accessionNumber": ["0001045810-25-000001", "0001045810-25-000002", "0001045810-25-000003"],
            "primaryDocument": ["future.htm", "release.htm", "annual.htm"], "items": ["9.01", "2.02,9.01", ""]}}}
        with patch.object(c, "utc_now", return_value=now), patch.object(c, "fetch_submissions", return_value=filings), \
                patch.object(c, "_yahoo_news", side_effect=ProviderError("HTTP 429")), \
                patch.object(c, "_google_news", return_value=([article], "https://news.google.com/rss")):
            result = c.context("NVDA", macro=False)
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["data"]["news"], [article])
        self.assertEqual(len(result["data"]["filings"]), 2)
        self.assertTrue(result["data"]["filings"][0]["earnings_release_candidate"])
        self.assertTrue(result["data"]["filings"][0]["url"].endswith("release.htm"))

    def test_fred_missing_month_keeps_separate_comparable_rate(self):
        raw = b"observation_date,CPIAUCSL\n2024-09-01,100\n2024-10-01,\n2025-09-01,103\n2025-10-01,104\n2025-11-01,105\n"
        with patch.object(c, "get_bytes", return_value=raw):
            result = c._fred("CPIAUCSL", date(2025, 10, 30))
        self.assertEqual(result["latest"], {"observation_date": "2025-10-01", "value": 104.0})
        self.assertIsNone(result["year_over_year_pct"])
        self.assertEqual(result["latest_comparable_year_over_year"]["observation_date"], "2025-09-01")
        self.assertAlmostEqual(result["latest_comparable_year_over_year"]["pct"], 3)

    def test_unsafe_filing_and_news_links_are_excluded(self):
        self.assertIsNone(c.filing_url("0001045810", "0001045810-25-000001", "../file"))
        self.assertIsNone(c._article("Headline", "2025-02-20", "", "javascript:alert(1)", "Vendor"))


if __name__ == "__main__":
    unittest.main()
