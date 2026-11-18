"""Archive row formatting (T-0186). Run: python3 -m unittest tests.test_archive_row"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import build  # noqa: E402


class ArchiveCell(unittest.TestCase):
    def test_cheap_window(self):
        c = build.archive_cell(95.26, "03–06")
        self.assertTrue(c.startswith("03–06 · 9,5 c/kWh"))
        self.assertLess(c.index("c/kWh"), c.index("€/MWh"))
        self.assertIn("(95.26 €/MWh)", c)

    def test_day_average_negative_and_zero(self):
        self.assertTrue(build.archive_cell(-12.5).startswith("-1,3 c/kWh"))
        self.assertTrue(build.archive_cell(-0.4).startswith("0,0 c/kWh"))
        self.assertTrue(build.archive_cell(0.0).startswith("0,0 c/kWh"))


if __name__ == "__main__":
    unittest.main()
