from datetime import date, timedelta
import math
import unittest

from analytics import backtest, benchmark_comparison, max_drawdown, rsi, technical


def candles(prices, *, start=date(2025, 1, 1), opens=None, interval="1d"):
    bars = []
    for i, price in enumerate(prices):
        opened = price if opens is None else opens[i]
        bars.append({"date": (start + timedelta(days=i)).isoformat(),
                     "open": opened, "high": max(opened, price) + 1,
                     "low": min(opened, price) - 1, "close": price, "volume": 100,
                     "complete": True})
    return {"interval": interval, "adjustment": "split_only", "bars": bars, "currency": "USD",
            "source": {"provider": "unit_test"}}


class IndicatorsTest(unittest.TestCase):
    def test_constant_market_and_unfinished_bar(self):
        history = candles([20] * 205 + [42])
        history["bars"][-1]["complete"] = False
        output = technical(history)
        for value in (*output["ma"].values(), *output["ema"].values()):
            self.assertAlmostEqual(value, 20)
        self.assertEqual(output["rsi_14"], 50)
        self.assertEqual(output["bollinger_20_2"], {"lower": 20, "middle": 20, "upper": 20})
        self.assertAlmostEqual(output["macd"]["histogram"], 0)
        self.assertAlmostEqual(output["kdj_9_3_3"]["j"], 50)
        self.assertEqual(output["atr_14"], 2)
        self.assertEqual(output["volume"]["ratio_20"], 1)
        self.assertEqual(output["bars"], 205)
        self.assertEqual(output["performance"]["max_drawdown_pct"], 0)

    def test_wilder_reference_and_gap_true_range(self):
        prices = [44.34, 44.09, 44.15, 43.61, 44.33, 44.83, 45.10, 45.42, 45.84,
                  46.08, 45.89, 46.03, 45.61, 46.28, 46.28, 46.00, 46.03]
        self.assertAlmostEqual(rsi(prices[:15]), 70.464135, places=5)
        self.assertAlmostEqual(rsi(prices[:16]), 66.249619, places=5)
        result = technical(candles([20] * 14 + [30]))
        self.assertAlmostEqual(result["atr_14"], (13 * 2 + 11) / 14)
        self.assertAlmostEqual(result["kdj_9_3_3"]["k"], (2 * 50 + 100 * 11 / 12) / 3)
        self.assertIsNone(result["ma"]["20"])

    def test_missing_volume_and_negative_drawdown(self):
        history = candles([20] * 21 + [30, 15])
        history["bars"][-2]["volume"] = None
        output = technical(history)
        self.assertIsNone(output["volume"]["ratio_20"])
        self.assertEqual(output["performance"]["max_drawdown_pct"], -50)
        self.assertAlmostEqual(max_drawdown([0.95, 1.1, 1.08], initial=1), -0.05)

    def test_benchmark_exact_common_dates_and_basis(self):
        a = candles([20, 25, 30, 40])
        b = candles([10, 11, 12, 11], start=date(2025, 1, 2))
        output = benchmark_comparison(a, b, "INDEX")
        self.assertEqual(output["common_bars"], 3)
        self.assertEqual(output["start"], "2025-01-02")
        self.assertEqual(output["end"], "2025-01-04")
        self.assertAlmostEqual(output["excess_pp"], 40)
        self.assertEqual(output["stock_basis"], "split_only")


class BacktestTest(unittest.TestCase):
    def test_next_open_with_two_sided_cost_and_partial_excluded(self):
        history = candles([10, 11, 12, 14, 8, 7, 30], opens=[10, 11, 12, 20, 10, 6, 31])
        history["bars"][-1]["complete"] = False
        output = backtest(history, 2, 3, 100)
        entry, _, exit_order = history["bars"][3:6]
        self.assertEqual([o["execution_date"] for o in output["orders"]], [entry["date"], exit_order["date"]])
        self.assertEqual(output["orders"][0]["signal_date"], history["bars"][2]["date"])
        self.assertAlmostEqual(output["total_return_pct"], 100 * (6 * 0.99 / (20 * 1.01) - 1))
        self.assertEqual(output["end"], exit_order["date"])
        self.assertEqual(output["closed_round_trips"], 1)
        self.assertTrue(math.isfinite(output["sharpe_zero_risk_free"]))

    def test_unverified_adjustment_requires_opt_in(self):
        history = candles([20, 22, 24, 26, 28])
        history["adjustment"] = "unknown"
        with self.assertRaisesRegex(ValueError, "adjustment"):
            backtest(history, 2, 3, 0)
        self.assertEqual(backtest(history, 2, 3, 0, allow_unverified=True)["price_basis"], "unknown")
