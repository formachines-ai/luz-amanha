"""One identical disclosure string in the available and unavailable JSON shapes (T-0093)."""
import datetime as dt
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import build  # noqa: E402


class Disclosure(unittest.TestCase):
    def test_same(self):
        built = dt.datetime(2026, 11, 3, 13, 15, tzinfo=dt.timezone.utc)
        day = dt.date(2026, 11, 3)
        files = {day: build.parse_omie((ROOT / "data" / "fixtures" / "marginalpdbcpt_20261103.1").read_text(encoding="latin-1"))}
        hours = build.lisbon_hours(day, files)
        avail = build.day_json(day, hours, build.stats(hours), build.comparison(day, files), build.load_tariff(), built, build.omie_filename(day))
        un1 = build.unavailable_json(dt.date(2026, 11, 4), built, 404, None, with_note=True)
        un2 = build.unavailable_json(day, built)
        self.assertTrue(avail["disclosure"])
        self.assertEqual(avail["disclosure"], un1["disclosure"])
        self.assertEqual(avail["disclosure"], un2["disclosure"])
        self.assertIn("AI agents", avail["disclosure"])
        self.assertIn("Dmitriy", avail["disclosure"])


if __name__ == "__main__":
    unittest.main()
