from datetime import date, timedelta
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from stock import execute

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "stock.py"


class CliTest(unittest.TestCase):
    def test_simple_quote_does_not_run_other_branches(self):
        output = {"status": "ok", "quote": {"price": 2}, "errors": []}
        with patch("market.quote", return_value=output) as quote:
            result = execute({"action": "quote", "symbol": "NASDAQ:NVDA"})
        quote.assert_called_once_with("NVDA", providers=None, as_of=None)
        self.assertEqual(result["data"], output)

    def test_json_input_and_local_snapshot_cutoff(self):
        bars = [{"date": (date(2025, 1, 1) + timedelta(days=i)).isoformat(),
                 "open": 20 + i, "high": 21 + i, "low": 19 + i,
                 "close": 20 + i, "volume": 10, "complete": True} for i in range(45)]
        payload = {"symbol": "NVDA", "data": {"history": {"bars": bars, "interval": "1d",
                                                         "adjustment": "split_only"}}}
        with tempfile.TemporaryDirectory() as work:
            path = Path(work) / "a history export.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            request = {"action": "technical", "symbol": "NVDA",
                       "history_file": str(path), "as_of": "2025-02-03"}
            run = subprocess.run([sys.executable, str(SCRIPT), "--json-input", "-"],
                                 input=json.dumps(request), text=True, capture_output=True, check=True)
        result = json.loads(run.stdout)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["data"]["technical"]["as_of"], "2025-02-03")
        self.assertEqual(result["data"]["technical"]["source"]["provider"], "local_history_snapshot")

    def test_invalid_action_option_returns_json_error(self):
        run = subprocess.run([sys.executable, str(SCRIPT), "--json-input", "-"],
                             input='{"action":"quote","symbol":"NVDA","peers":["AMD"]}',
                             text=True, capture_output=True)
        self.assertEqual(run.returncode, 2)
        self.assertIn("unsupported fields", json.loads(run.stdout)["error"]["message"])
