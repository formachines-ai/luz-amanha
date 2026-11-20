"""start_local / start_utc in hours[] (T-0224): offsets incl. DST day 2026-10-25; Lisbon-time line on pages.
Builds offline into a temp dir. Run: python3 -m unittest tests.test_start_local"""
import datetime as dt
import json
import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import build  # noqa: E402


class StartLocal(unittest.TestCase):
    def test_dst_day_synthetic(self):
        day = dt.date(2026, 10, 25)
        fx = build.parse_omie((ROOT / "data" / "fixtures" / "marginalpdbcpt_20261025.1").read_text(encoding="latin-1"))
        built = dt.datetime(2026, 10, 24, 13, 15, tzinfo=dt.timezone.utc)
        hours = build.lisbon_hours(day, {day: fx})
        j = build.day_json(day, hours, build.stats(hours), build.comparison(day, {day: fx}), build.load_tariff(), built, "x")
        self.assertEqual(len(j["hours"]), 25)
        offs = {h["start_local"][-6:] for h in j["hours"]}
        self.assertEqual(offs, {"+01:00", "+00:00"})
        self.assertEqual(j["hours"][0]["start_local"], "2026-10-25T00:00:00+01:00")
        self.assertEqual(j["hours"][-1]["start_local"], "2026-10-25T23:00:00+00:00")
        for h in j["hours"]:
            self.assertRegex(h["start_utc"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")
            self.assertEqual(dt.datetime.fromisoformat(h["start_local"]), dt.datetime.fromisoformat(h["start_utc"][:-1] + "+00:00"))
            self.assertIn("hour", h)

    def test_temp_build(self):
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t)
            shutil.copytree(ROOT / "data" / "omie", tmp / "omie")
            days = sorted(p.stem for p in (tmp / "omie").glob("????-??-??.txt"))
            target = dt.date.fromisoformat(days[-1])
            with mock.patch.object(build, "DATA_DIR", tmp / "omie"), mock.patch.object(build, "PUBLIC_DIR", tmp / "public"), \
                    mock.patch.object(build, "ROOT", tmp), mock.patch("urllib.request.urlopen", side_effect=OSError("off")):
                build.build(target, offline=True)
            out = tmp / "public"
            n = 0
            for p in out.glob("d/????-??-??/index.json"):
                j = json.loads(p.read_text(encoding="utf-8"))
                self.assertEqual(j["timezone"], "Europe/Lisbon")
                for h in j["hours"]:
                    n += 1
                    self.assertRegex(h["start_utc"], r"Z$")
                    self.assertIn(h["start_local"][-6:], ("+00:00", "+01:00"))
            self.assertGreater(n, 24)
            for name in ("tomorrow.json", "today.json"):
                j = json.loads((out / name).read_text(encoding="utf-8"))
                self.assertEqual(j["timezone"], "Europe/Lisbon")
            for pg in (out / "index.html", *out.glob("d/????-??-??/index.html")):
                h = pg.read_text(encoding="utf-8")
                self.assertIn("Horas de Lisboa", h)
                self.assertIn("Lisbon time", h)
            self.assertIn("start_local", (out / "llms.txt").read_text(encoding="utf-8"))
            self.assertIn("start_local", (out / "ha" / "index.html").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
