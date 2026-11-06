"""Status text names the next build slot (T-0082). Run: python3 -m unittest tests.test_status_text"""
import datetime as dt
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import build  # noqa: E402


def utc(h, m):
    return dt.datetime(2026, 11, 6, h, m, tzinfo=dt.timezone.utc)


class StatusText(unittest.TestCase):
    def test_slots(self):
        self.assertEqual(build.SCHEDULE_UTC, ("13:15", "13:45", "14:30", "16:00"))

    def test_next_slot(self):
        for (h, m), want in [((13, 20), "13:45"), ((13, 50), "14:30"), ((14, 35), "16:00"), ((16, 5), None)]:
            self.assertEqual(build.next_slot(utc(h, m)), want)

    def test_text(self):
        t = {}
        for (h, m), want in [((13, 20), "13:45"), ((13, 50), "14:30"), ((14, 35), "16:00"), ((16, 5), None)]:
            built = utc(h, m)
            build.MISSING.clear()
            build.MISSING.update(date=dt.date(2026, 11, 7), last_attempt=built, next_attempt=build.next_slot(built), code=404)
            for lang, key in (("pt", "próxima tentativa"), ("en", "next attempt")):
                s = build.status_text(lang, t, "status_missing")
                self.assertIn(f"{h:02d}:{m:02d} UTC", s)
                if want:
                    self.assertIn(f"{key}: {want} UTC", s)
                else:
                    self.assertNotIn(key, s)
        build.MISSING.clear()


if __name__ == "__main__":
    unittest.main()
