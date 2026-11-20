"""T-0226: dated pages, sitemap.xml, feed.xml and /go/* pages do not depend on the build clock.
Builds offline twice from the cached data/omie/ into two temp dirs with a shifted clock and compares bytes.
Run: python3 -m unittest tests.test_stable_dates"""
import datetime as dt
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


def build_into(tmp: Path, now: dt.datetime, target: dt.date) -> Path:
    shutil.copytree(ROOT / "data" / "omie", tmp / "omie")
    with mock.patch.object(build, "DATA_DIR", tmp / "omie"), mock.patch.object(build, "PUBLIC_DIR", tmp / "public"), \
            mock.patch.object(build, "ROOT", tmp), mock.patch("urllib.request.urlopen", side_effect=OSError("network disabled")):
        build.build(target, offline=True, now=now)
    return tmp / "public"


class StableDates(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.t1, cls.t2 = tempfile.TemporaryDirectory(), tempfile.TemporaryDirectory()
        days = sorted(p.stem for p in (ROOT / "data" / "omie").glob("????-??-??.txt"))
        target = dt.date.fromisoformat(days[-1])
        base = dt.datetime.combine(target - dt.timedelta(days=1), dt.time(13, 15), tzinfo=dt.timezone.utc)
        cls.a = build_into(Path(cls.t1.name), base, target)
        cls.b = build_into(Path(cls.t2.name), base + dt.timedelta(hours=7, minutes=30), target)

    @classmethod
    def tearDownClass(cls):
        cls.t1.cleanup()
        cls.t2.cleanup()

    def test_identical(self):
        names = ["sitemap.xml"] + [str(p.relative_to(self.a)) for p in sorted(self.a.glob("go/**/*.html"))]
        dated = sorted(p.relative_to(self.a) for p in (self.a / "d").glob("????-??-??/**/*") if p.is_file())
        self.assertTrue(dated and len(names) > 2)
        for n in names + [str(p) for p in dated]:
            self.assertEqual((self.a / n).read_bytes(), (self.b / n).read_bytes(), n)

    def test_lastmod_follows_data(self):
        sm = (self.a / "sitemap.xml").read_text(encoding="utf-8")
        m = re.search(r"/d/2026-11-10/</loc><lastmod>([\d-]+)<", sm)
        self.assertIsNotNone(m)
        self.assertLessEqual(m.group(1), "2026-11-12")

    def test_new_file_updates_day(self):
        """A changed OMIE file D+1 must move the lastmod of page D (no frozen dates)."""
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t)
            shutil.copytree(ROOT / "data" / "omie", tmp / "omie")
            f = tmp / "omie" / "2026-11-11.txt"
            f.write_bytes(f.read_bytes() + b"\n")
            with mock.patch.object(build, "DATA_DIR", tmp / "omie"):
                build.load_stamps(dt.datetime(2030, 1, 5, 12, 0, tzinfo=dt.timezone.utc))
                self.assertEqual(build.day_stamp(dt.date(2026, 11, 10)).date(), dt.date(2030, 1, 5))
            build.STAMPS.clear()


if __name__ == "__main__":
    unittest.main()
