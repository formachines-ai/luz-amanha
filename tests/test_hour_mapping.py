"""Lisbon 23:00 of day D = mean of the first 4 quarters of the D+1 file (T-0088). Run: python3 -m unittest tests.test_hour_mapping"""
import datetime as dt
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import build  # noqa: E402

FIXTURE = (ROOT / "data" / "fixtures" / "marginalpdbcpt_20261103.1").read_text(encoding="latin-1")
D7, D8 = dt.date(2026, 11, 7), dt.date(2026, 11, 8)


def parsed(day):
    return build.parse_omie(FIXTURE.replace("2026;11;03;", f"{day:%Y;%m;%d};"))


class HourMapping(unittest.TestCase):
    def test_next_file_present_gives_24(self):
        files = {D7: parsed(D7), D8: parsed(D8)}
        hrs = build.lisbon_hours(D7, files)
        self.assertEqual(len(hrs), 24)
        self.assertEqual(hrs[23]["label"], "23:00")
        want = round(sum(p for _, p, _ in files[D8]["periods"][:4]) / 4, 2)
        self.assertEqual(hrs[23]["pt"], want)
        self.assertTrue(build.stats(hrs)["complete"])

    def test_next_file_absent_gives_null(self):
        hrs = build.lisbon_hours(D7, {D7: parsed(D7)})
        self.assertEqual(len(hrs), 24)
        self.assertIsNone(hrs[23]["pt"])
        self.assertTrue(all(h["pt"] is not None for h in hrs[:23]))
        st = build.stats(hrs)
        self.assertFalse(st["complete"])
        self.assertEqual(st["available"], 23)


if __name__ == "__main__":
    unittest.main()
