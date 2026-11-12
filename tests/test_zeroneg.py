"""Zero/negative hour label and count line (T-0145). Run: python3 -m unittest tests.test_zeroneg"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import build  # noqa: E402


def hrs(prices):
    return [{"label": f"{i:02d}:00", "pt": p} for i, p in enumerate(prices)]


def table(hours, lang="pt"):
    return build.hours_table(hours, {}, {"enabled": False}, lang)


class ZeroNeg(unittest.TestCase):
    def test_labelled_and_count(self):
        h = hrs([-1.50, 0.00, 0.01, 50.0])
        t = table(h)
        self.assertEqual(t.count('class="zeroneg"'), 2)
        rows = t.split("<tr")
        self.assertIn("zeroneg", rows[2])  # 00:00 (-1.50)
        self.assertIn("zeroneg", rows[3])  # 01:00 (0.00)
        self.assertNotIn("zeroneg", rows[4])  # 02:00 (0.01)
        self.assertNotIn("zeroneg", rows[5])
        self.assertIn("2 horas", build.zeroneg_line(h, "pt"))
        self.assertIn("00, 01", build.zeroneg_line(h, "pt"))
        en = build.zeroneg_line(h, "en")
        self.assertIn("2 hours", en)
        self.assertIn("00, 01", en)
        self.assertIn("zero/negative", table(h, "en"))

    def test_no_line_when_none(self):
        h = hrs([0.01, 10.0, 99.0])
        self.assertEqual(build.zeroneg_line(h, "pt"), "")
        self.assertEqual(build.zeroneg_line(h, "en"), "")
        self.assertNotIn("zeroneg", table(h))

    def test_null_hour_not_labelled(self):
        h = hrs([None, 5.0])
        self.assertEqual(build.zeroneg_line(h, "pt"), "")
        self.assertNotIn("zeroneg", table(h))

    def test_single_hour_singular(self):
        self.assertIn("1 hora ", build.zeroneg_line(hrs([-3.0, 4.0]), "pt"))


if __name__ == "__main__":
    unittest.main()
