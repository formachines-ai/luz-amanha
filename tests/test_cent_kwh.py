"""Headline c/kWh conversion (T-0178). Run: python3 -m unittest tests.test_cent_kwh"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import build  # noqa: E402


class CentKwh(unittest.TestCase):
    def test_convert(self):
        self.assertEqual(build.cent_kwh(181.0, "pt"), "18,1")
        self.assertEqual(build.cent_kwh(181.0, "en"), "18.1")
        self.assertEqual(build.cent_kwh(95.26, "en"), "9.5")

    def test_negative_and_zero(self):
        self.assertEqual(build.cent_kwh(-12.5, "en"), "-1.2")
        self.assertEqual(build.cent_kwh(-12.5, "pt"), "-1,2")
        self.assertEqual(build.cent_kwh(-0.4, "pt"), "0,0")
        self.assertEqual(build.cent_kwh(0.0, "en"), "0.0")

    def test_pair_order(self):
        p = build.price_pair(-30.0, "pt")
        self.assertTrue(p.startswith("-3,0 c/kWh"))
        self.assertIn("-30.00 €/MWh", p)
        self.assertLess(p.index("c/kWh"), p.index("€/MWh"))


if __name__ == "__main__":
    unittest.main()
