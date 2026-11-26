from datetime import date
import unittest
from unittest.mock import patch

from valuation import historical_valuation


class HistoricalValuationTest(unittest.TestCase):
    def test_next_session_first_filed_facts_with_split_guard(self):
        history = {"currency": "USD", "interval": "1d", "adjustment": "split_dividend", "splits": [],
                   "bars": [{"date": day, "close": 40, "raw_close": 42, "complete": True}
                            for day in ("2025-08-21", "2025-08-26", "2025-08-27")]}
        submissions = {"filings": {"recent": {"form": ["10-Q"], "filingDate": ["2025-08-26"]}}}
        known = {"currency": "USD", "shares_outstanding_cover_page": {
            "as_of": "2025-08-23", "filed_on": "2025-08-26", "value": 10},
            "ttm": {"end": "2025-07-30", "facts": {
                "net_income": {"value": 21, "filed_on": "2025-08-26"},
                "revenue": {"value": 84, "filed_on": "2025-08-26"}}}}
        with patch("valuation.fetch_submissions", return_value=submissions), \
             patch("valuation.fetch_facts", return_value={}), \
             patch("valuation._parse_sec_facts", return_value=known) as parse:
            result = historical_valuation({"cik": "0000000001"}, history)
            parse.assert_called_with({}, date(2025, 8, 26), first_filed=True)
            point = result["observations"][0]
            self.assertEqual(point["session"], "2025-08-27")
            self.assertEqual(point["pe_ttm"], 20)
            self.assertEqual(point["ps_ttm"], 5)
            self.assertIsNone(point["pb"])
            history["splits"] = [{"date": "2025-08-28", "ratio": 2}]
            self.assertEqual(historical_valuation({"cik": "0000000001"}, history)["status"], "unavailable")
