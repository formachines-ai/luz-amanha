"""T-0254: missing_hours_note only when 23:00 is the only null hour. Run: python3 -m unittest tests.test_missing_hours_note"""
import unittest

import build

PT = "A hora 23:00 é publicada com o ficheiro OMIE do dia seguinte."
EN = "The 23:00 hour comes with the next day's OMIE file."


def day(null):
    return [{"label": f"{i:02d}:00", "pt": None if f"{i:02d}:00" in null else 10.0 + i} for i in range(24)]


class Note(unittest.TestCase):
    def test_cases(self):
        for null, want in (({"23:00"}, True), (set(), False), ({"05:00"}, False), ({"05:00", "23:00"}, False)):
            self.assertEqual(build.only_23_missing(day(null)), want, null)
        self.assertEqual(build.T["pt"]["hour23"], PT)
        self.assertEqual(build.T["en"]["hour23"], EN)

    def test_json(self):
        import datetime as dt
        for null, want in (({"23:00"}, True), (set(), False), ({"05:00"}, False)):
            hs = day(null)
            for h in hs:
                h.update(start_utc="2026-11-03T00:00:00+00:00", es=None)
            st = build.stats(hs)
            j = build.day_json(dt.date(2026, 11, 3), hs, st, {}, {}, dt.datetime(2026, 11, 2, tzinfo=dt.timezone.utc), None)
            self.assertEqual(j["missing_hours_note"], {"pt": PT, "en": EN} if want else None, null)


if __name__ == "__main__":
    unittest.main()
