"""today/tomorrow follow the Lisbon calendar date at build time (T-0086). Run: python3 -m unittest tests.test_lisbon_day"""
import datetime as dt
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import build  # noqa: E402

FIXTURE = (ROOT / "data" / "fixtures" / "marginalpdbcpt_20261103.1").read_text(encoding="latin-1")


def shifted(day: dt.date) -> str:
    return FIXTURE.replace("2026;11;03;", f"{day:%Y;%m;%d};")


def run(now: dt.datetime, days: list[dt.date]):
    tmp = Path(tempfile.mkdtemp())
    (tmp / "omie").mkdir()
    for d in days:
        (tmp / "omie" / f"{d.isoformat()}.txt").write_text(shifted(d), encoding="latin-1")
    lisbon = now.astimezone(build.DISPLAY_TZ).date()
    with mock.patch.object(build, "DATA_DIR", tmp / "omie"), mock.patch.object(build, "PUBLIC_DIR", tmp / "public"), \
            mock.patch.object(build, "ROOT", tmp), mock.patch("urllib.request.urlopen", side_effect=OSError("no network")):
        build.build(lisbon + dt.timedelta(days=1), offline=True, now=now)
    pub = tmp / "public"
    rd = lambda n: json.loads((pub / n).read_text(encoding="utf-8"))
    return rd("today.json"), rd("tomorrow.json"), (pub / "index.html").read_text(encoding="utf-8")


D6, D7, D8 = dt.date(2026, 11, 6), dt.date(2026, 11, 7), dt.date(2026, 11, 8)


class LisbonDay(unittest.TestCase):
    def test_after_midnight_lisbon(self):
        now = dt.datetime(2026, 11, 7, 0, 30, tzinfo=build.DISPLAY_TZ)  # 00:30 Lisbon on 07.11
        today, tomorrow, index = run(now, [D6, D7])
        self.assertEqual(today["date"], "2026-11-07")
        self.assertTrue(today["available"])
        self.assertEqual(tomorrow["date"], "2026-11-08")
        self.assertFalse(tomorrow["available"])
        self.assertEqual(index.count("<h1"), 1)
        self.assertIn("Luz hoje — 07.11.2026", index)
        self.assertNotIn("Luz amanhã — 07.11", index)
        self.assertIn("08.11", index)
        self.assertIn("Última tentativa", index)
        self.assertIn("próxima tentativa: 13:15 UTC", index)
        self.assertEqual(index.count("<script"), index.count("<script type=\"application/ld+json\">"))  # only inert JSON-LD

    def test_midnight_utc_is_already_lisbon_date_in_summer_not_winter(self):
        # 23:30 UTC on 06.11 is 23:30 Lisbon (WET, UTC+0): still 06.11
        today, tomorrow, _ = run(dt.datetime(2026, 11, 6, 23, 30, tzinfo=dt.timezone.utc), [D6, D7])
        self.assertEqual(today["date"], "2026-11-06")
        self.assertEqual(tomorrow["date"], "2026-11-07")

    def test_tomorrow_file_present_1435_utc(self):
        now = dt.datetime(2026, 11, 6, 14, 35, tzinfo=dt.timezone.utc)
        today, tomorrow, index = run(now, [D6, D7])
        self.assertEqual(today["date"], "2026-11-06")
        self.assertEqual(tomorrow["date"], "2026-11-07")
        self.assertTrue(tomorrow["available"])
        self.assertIn("07.11", index)
        self.assertNotIn("ainda não temos", index.lower())
        self.assertEqual(index.count("<h1"), 1)


if __name__ == "__main__":
    unittest.main()
