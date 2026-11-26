"""Deterministic market data tests. No network or paid data subscriptions."""

import json
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import market

NOW = datetime(2026, 11, 25, 15, 15, tzinfo=timezone.utc)
NY = ZoneInfo("America/New_York")


def chart(*, adjusted=None, delay=None, market_time=None):
    timestamps = [int(datetime(2026, 11, day, 9, 30, tzinfo=NY).timestamp()) for day in [24, 25]]
    meta = {
        "currency": "USD", "exchangeTimezoneName": "America/New_York", "fullExchangeName": "NasdaqGS",
        "instrumentType": "EQUITY", "regularMarketTime": market_time or NOW.timestamp(),
        "regularMarketPrice": 108.0, "regularMarketChangePercent": 8.0, "regularMarketVolume": 13000,
        "currentTradingPeriod": {"regular": {"end": int(datetime(2026, 11, 25, 16, tzinfo=NY).timestamp())}},
    }
    if delay is not None:
        meta["exchangeDataDelayedBy"] = delay
    indicators = {
        "quote": [{"open": [92, 100], "high": [101, 111], "low": [90, 98],
                   "close": [100, 108], "volume": [20000, 13000]}],
        "adjclose": [{"adjclose": adjusted if adjusted is not None else [98.0, 108.0]}],
    }
    payload = {"chart": {"result": [{"timestamp": timestamps, "meta": meta, "indicators": indicators}],
                         "error": None}}
    return json.dumps(payload).encode()


def tencent_fields(values):
    fields = [""] * 50
    for position, value in values.items():
        fields[position] = str(value)
    return fields


class MarketTests(unittest.TestCase):
    def setUp(self):
        timing = patch.object(market, "_now", return_value=NOW)
        timing.start()
        self.addCleanup(timing.stop)

    def test_symbols_preserve_markets_and_reject_ambiguous_codes(self):
        expectations = {
            " nasdaq:nvda ": "NVDA", "BRK.B.US": "BRK-B", "BRK.B": "BRK-B", "hkex:700": "0700.HK",
            "00700.HK": "0700.HK", "600519.SH": "600519.SS", "SZSE:000001": "000001.SZ",
            "LSE:SHEL.L": "SHEL.L", "M&M.NS": "M&M.NS", "^gspc": "^GSPC", "TD.TO": "TD.TO",
        }
        for input_ticker, expected in expectations.items():
            with self.subTest(symbol=input_ticker):
                self.assertEqual(market.normalize_symbol(input_ticker), expected)
        for invalid in ("0700", "blah:ABC", "../etc", "https://example.com", ""):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                market.normalize_symbol(invalid)

    def test_yahoo_quote_timestamp_and_dividend_adjusted_history(self):
        with patch.object(market, "_http", return_value=(chart(), market.YAHOO)):
            result = market.market_data("NVDA", providers="yahoo")
        self.assertEqual(result["status"], "ok")
        quote = result["quote"]
        self.assertEqual(quote["price"], 108.0)
        self.assertEqual(quote["as_of"], "2026-11-25T10:15:00-05:00")
        self.assertEqual(quote["time_kind"], "freshness_unverified")
        self.assertEqual(quote["currency"], "USD")
        self.assertEqual(quote["previous_close"], 100.0)
        self.assertEqual(quote["change_absolute"], 8.0)
        self.assertIsNone(quote["turnover"])
        history = result["history"]
        self.assertEqual(history["adjustment"], "split_dividend")
        self.assertTrue(history["bars"][0]["complete"])
        self.assertFalse(history["bars"][1]["complete"])
        self.assertAlmostEqual(history["bars"][0]["open"], 92 * 0.98)
        self.assertEqual(history["bars"][0]["raw_close"], 100.0)
        self.assertEqual(history["bars"][0]["close"], 98.0)
        self.assertEqual(history["bars"][0]["volume"], 20000.0)

    def test_explicit_delay_indicator(self):
        with patch.object(market, "_http", return_value=(chart(delay=15), market.YAHOO)):
            quote = market.quote("NVDA", providers=["yahoo"])["quote"]
        self.assertEqual(quote["time_kind"], "provider_delayed")
        self.assertEqual(quote["delay_minutes"], 15)

    def test_historical_cutoff_excludes_future_and_live_price(self):
        with patch.object(market, "_http", return_value=(chart(), market.YAHOO)) as http:
            result = market.market_data("NVDA", providers=["yahoo"], as_of="2026-11-24")
        self.assertEqual(len(result["history"]["bars"]), 1)
        self.assertEqual(result["quote"]["price"], 100.0)
        self.assertEqual(result["quote"]["as_of"], "2026-11-24")
        self.assertEqual(result["quote"]["time_kind"], "historical_close")
        self.assertIn("period1", http.call_args.kwargs["params"])
        self.assertNotIn("range", http.call_args.kwargs["params"])

    def test_historical_intraday_cutoff_has_no_lookahead_daily_bar(self):
        with patch.object(market, "_http", return_value=(chart(), market.YAHOO)):
            result = market.history("NVDA", providers="yahoo", as_of="2026-11-25T09:45:00-05:00")
        self.assertNotIn("quote", result)
        self.assertEqual([row["date"] for row in result["history"]["bars"]], ["2026-11-24"])
        self.assertEqual(result["history"]["splits"], [])

    def test_yahoo_extracts_split_ratio_and_exchange_date(self):
        parsed = json.loads(chart())
        timestamp = int(datetime(2026, 11, 24, 9, 30, tzinfo=NY).timestamp())
        parsed["chart"]["result"][0]["events"] = {"splits": {
            str(timestamp): {"date": timestamp, "numerator": 4, "denominator": 1, "splitRatio": "4:1"},
        }}
        with patch.object(market, "_http", return_value=(json.dumps(parsed).encode(), market.YAHOO)):
            result = market.history("NVDA", providers="yahoo")
        self.assertEqual(result["history"]["splits"], [
            {"date": "2026-11-24", "ratio": 4.0, "numerator": 4.0, "denominator": 1.0},
        ])

    def test_missing_adjustments_yield_consistent_split_only_bars(self):
        observation = datetime(2026, 11, 25, 22, tzinfo=timezone.utc)
        with patch.object(market, "_now", return_value=observation):
            with patch.object(market, "_http", return_value=(chart(adjusted=[98, None]), market.YAHOO)):
                result = market.market_data("NVDA", providers=["yahoo"])
        self.assertEqual(result["history"]["adjustment"], "split_only")
        self.assertEqual([row["close"] for row in result["history"]["bars"]], [100, 108])

    def test_tencent_us_turnover_currency_and_company_name(self):
        quote_fields = tencent_fields({
            1: "英伟达", 2: "NVDA.OQ", 3: 108, 4: 100, 30: "2026-11-25 10:15:00",
            31: 8, 32: 8, 34: 98, 35: "USD", 36: 13000, 37: 1360000,
            45: 3000, 46: "NVIDIA Corporation",
        })
        body = ('v_usNVDA="' + "~".join(quote_fields) + '";').encode("gb18030")
        with patch.object(market, "_http", return_value=(body, market.TENCENT_QUOTE)):
            quote = market.quote("NVDA", providers="tencent")["quote"]
        self.assertEqual(quote["company_name"], "NVIDIA Corporation")
        self.assertEqual(quote["currency"], "USD")
        self.assertEqual(quote["turnover_currency"], "USD")
        self.assertEqual(quote["turnover"], 1360000)
        self.assertEqual(quote["volume_unit"], "shares")

    def test_tencent_mainland_amount_uses_base_currency_and_volume_units(self):
        quote_fields = tencent_fields({
            1: "贵州茅台", 2: "600519", 3: 1474.84, 4: 1460, 30: "20261125161439",
            31: 14.84, 32: 1.02, 35: "1474.84/46674/6842335769", 36: 46674, 37: 684234,
        })
        quote_bytes = ('v_sh600519="' + "~".join(quote_fields) + '";').encode("gb18030")
        klines = {"code": 0, "data": {"sh600519": {"qfqday": [["2026-11-24", "1462", "1460", "1464", "1456", "5555"]]}}}
        responses = [(quote_bytes, market.TENCENT_QUOTE), (json.dumps(klines).encode(), market.TENCENT_HISTORY)]
        with patch.object(market, "_http", side_effect=responses):
            result = market.market_data("600519.SH", providers="tencent")
        self.assertEqual(result["quote"]["turnover"], 6842335769)
        self.assertEqual(result["quote"]["volume_unit"], "lots_100")
        self.assertEqual(result["quote"]["as_of"], "2026-11-25T16:14:39+08:00")
        self.assertEqual(result["quote"]["time_kind"], "historical_close")
        self.assertEqual(result["history"]["adjustment"], "provider_forward")
        self.assertEqual(result["history"]["bars"][0]["close"], 1460)

    def test_yahoo_and_stooq_failure_use_sina_end_of_day(self):
        sina_bytes = (b'/*<script>location.href="//sina.com";</script>*/\n'
                      b'var x=([{"d":"2026-11-24","o":"92","h":"101",'
                      b'"l":"90","c":"100","v":"20000","a":"1900000"}]);')
        responses = [market.ProviderError("HTTP 429"), (b"<html>Challenge</html>", market.STOOQ),
                     (sina_bytes, market.SINA)]
        with patch.object(market, "_http", side_effect=responses):
            result = market.market_data("NVDA", providers=["yahoo", "stooq", "sina"])
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["quote"]["time_kind"], "historical_close")
        self.assertEqual(result["quote"]["turnover"], 1900000)
        self.assertEqual(result["history"]["source"]["provider"], "sina")
        self.assertEqual(result["history"]["adjustment"], "unknown")
        self.assertEqual([err["provider"] for err in result["errors"]], ["yahoo", "stooq"])
        self.assertIn("anti-bot", result["errors"][1]["message"])

    def test_yfinance_sdk_failure_uses_an_independent_history_feed(self):
        sdk = Mock(side_effect=TimeoutError("yfinance request timed out"))
        sina = (b'var x=([{"d":"2026-11-24","o":"92","h":"101","l":"90",'
                b'"c":"100","v":"20000"}]);')
        with patch.dict(market._PROVIDERS, {"yfinance": sdk}):
            with patch.object(market, "_http", return_value=(sina, market.SINA)) as http:
                result = market.history("NVDA", providers=["yfinance", "sina"])
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["history"]["source"]["provider"], "sina")
        self.assertEqual(result["errors"], [
            {"provider": "yfinance", "operation": "history", "message": "yfinance request timed out"},
        ])
        http.assert_called_once()

    def test_rate_limit_short_retry_and_long_retry_skips(self):
        error = HTTPError(market.YAHOO, 429, "Too Many Requests", {"Retry-After": "60"}, None)
        with patch.object(market, "urlopen", side_effect=error) as request:
            with patch.object(market.time, "sleep") as sleep:
                with self.assertRaisesRegex(market.ProviderError, "429"):
                    market._http(market.YAHOO)
        self.assertEqual(request.call_count, 1)
        sleep.assert_not_called()
        with patch.object(market, "urlopen", side_effect=URLError("offline")) as request:
            with patch.object(market.time, "sleep") as sleep:
                with self.assertRaisesRegex(market.ProviderError, "offline"):
                    market._http(market.YAHOO)
        self.assertEqual(request.call_count, 2)
        sleep.assert_called_once()

    def test_unavailable_data_has_errors_and_no_invented_fields(self):
        with patch.object(market, "_http", side_effect=market.ProviderError("offline")):
            result = market.market_data("NVDA", providers="yahoo,stooq")
        self.assertEqual(result["status"], "unavailable")
        self.assertIsNone(result["quote"])
        self.assertIsNone(result["history"])
        self.assertEqual(len(result["errors"]), 2)

    def test_other_currency_quote_units(self):
        metadata = market._market_metadata({"currency": "GBp", "exchangeTimezoneName": "Europe/London"})
        self.assertEqual(metadata["currency"], "GBP")
        self.assertEqual(metadata["price_unit"], "GBp")


if __name__ == "__main__":
    unittest.main()
