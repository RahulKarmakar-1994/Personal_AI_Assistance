import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

import finance


CSV = b"""transaction_date,scheme_name,amfi_code,folio,transaction_type,amount,units,nav,description
2025-01-01,Example Equity Fund - Direct Growth,123456,F-1,purchase,10000.00,100.000,100.00,Initial purchase
2025-06-01,Example Equity Fund - Direct Growth,123456,F-1,sip,5500.00,50.000,110.00,SIP
2026-01-01,Example Equity Fund - Direct Growth,123456,F-1,redemption,7200.00,60.000,120.00,Partial redemption
"""


class FinanceTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.path = Path(self.folder.name) / "finance.db"
        finance.initialize(self.path)

    def tearDown(self):
        self.folder.cleanup()

    def test_import_is_idempotent(self):
        first = finance.import_csv(self.path, CSV, "transactions.csv")
        second = finance.import_csv(self.path, CSV, "transactions.csv")
        self.assertEqual(first["accepted_count"], 3)
        self.assertTrue(second["duplicate"])
        with finance.connect(self.path) as connection:
            count = connection.execute("SELECT COUNT(*) count FROM mf_transactions").fetchone()["count"]
        self.assertEqual(count, 3)

    def test_fifo_cost_and_value_use_decimal(self):
        finance.import_csv(self.path, CSV, "transactions.csv")
        with finance.connect(self.path) as connection:
            scheme_id = connection.execute("SELECT id FROM mf_schemes").fetchone()["id"]
            connection.execute(
                "INSERT INTO mf_nav_history(scheme_id,nav_date,nav,fetched_at) VALUES(?,?,?,?)",
                (scheme_id, "2026-01-02", "125.1234", "2026-01-02T20:00:00Z"),
            )
        result = finance.portfolio(self.path, "2026-01-02")
        holding = result["holdings"][0]
        self.assertEqual(holding["units"], "90.000")
        self.assertEqual(holding["cost"], "9500.00")
        self.assertEqual(holding["market_value"], "11261.11")
        self.assertEqual(holding["realized_gain"], "1200.00")
        self.assertIsNotNone(result["xirr_percent"])

    def test_duplicate_transaction_across_files_is_not_inserted(self):
        first = finance.import_csv(self.path, CSV, "one.csv")
        second_bytes = CSV + b"\n"
        second = finance.import_csv(self.path, second_bytes, "two.csv")
        self.assertEqual(first["accepted_count"], 3)
        self.assertEqual(second["accepted_count"], 0)
        self.assertEqual(second["report"]["existing_transactions"], 3)

    def test_invalid_rows_are_reported_not_guessed(self):
        invalid = b"transaction_date,scheme_name,transaction_type,amount,units\nnot-a-date,Fund,purchase,5000,10\n"
        result = finance.import_csv(self.path, invalid, "bad.csv")
        self.assertEqual(result["accepted_count"], 0)
        self.assertEqual(result["rejected_count"], 1)
        self.assertIn("recognizable date", result["report"]["errors"][0]["error"])

    def test_missing_nav_withholds_portfolio_totals(self):
        finance.import_csv(self.path, CSV.splitlines()[0] + b"\n" + CSV.splitlines()[1] + b"\n", "purchase.csv")
        result = finance.portfolio(self.path, "2026-01-02")
        self.assertIsNone(result["market_value"])
        self.assertIsNone(result["unrealized_gain"])
        self.assertIsNone(result["xirr_percent"])
        self.assertIn("withheld", result["warnings"][0])

    def test_amfi_parser(self):
        sample = """Open Ended Schemes (Equity Scheme - Large Cap Fund)\nScheme Code;ISIN Div Payout/ ISIN Growth;ISIN Div Reinvestment;Scheme Name;Net Asset Value;Date\n123456;INF000A01000;;Example Equity Fund - Direct Growth;125.1234;02-Jan-2026\n"""
        records = finance.parse_amfi(sample)
        self.assertEqual(records[0]["amfi_code"], "123456")
        self.assertEqual(Decimal(records[0]["nav"]), Decimal("125.1234"))
        self.assertIn("Large Cap", records[0]["category"])

    def test_current_amfi_eight_column_parser(self):
        sample = """Scheme Code;ISIN Div Payout/ ISIN Growth;ISIN Div Reinvestment;Scheme Name;Plan;Option;Net Asset Value;Date\nOpen Ended Schemes(Debt Scheme - Banking and PSU Fund)\nAxis Mutual Fund\n135762;INF846K01WO1;-;Axis Children's Fund;Direct Plan;Growth Option;31.0915;11-Nov-2026\n"""
        records = finance.parse_amfi(sample)
        self.assertEqual(records[0]["plan"], "Direct Plan")
        self.assertEqual(records[0]["option_name"], "Growth Option")
        self.assertIn("Banking and PSU", records[0]["category"])


if __name__ == "__main__":
    unittest.main()
