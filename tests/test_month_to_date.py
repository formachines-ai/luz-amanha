"""month_to_date equals mean of cached hourly prices (T-0222). Run: python3 -m unittest tests.test_month_to_date"""
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


class MonthToDate(unittest.TestCase):
    def test_matches_dated_pages(self):
        m = json.loads((ROOT / "public/today.json").read_text(encoding="utf-8"))["month_to_date"]
        vals = []
        for f in sorted((ROOT / "public/d").glob(m["month"] + "-*/index.json")):
            if m["from"] <= f.parent.name <= m["to"]:
                j = json.loads(f.read_text(encoding="utf-8"))
                vals += [h["price_pt_eur_mwh"] for h in j.get("hours", [])]
        vals = [v for v in vals if v is not None]
        self.assertEqual(len(vals), m["hours"])
        self.assertAlmostEqual(sum(vals) / len(vals), m["avg_eur_mwh"], places=2)
        self.assertEqual(m["avg_cent_kwh"], round(m["avg_eur_mwh"] / 10 + 1e-9, 1))
        self.assertEqual(m["avg_pt_eur_mwh"], m["avg_eur_mwh"])


if __name__ == "__main__":
    unittest.main()
