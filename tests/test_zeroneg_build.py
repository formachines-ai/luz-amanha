"""T-0234: the 'zero or negative price is real' note on built day pages. Builds offline into a temp dir from the cached
data/omie/ plus one synthetic day (2026-11-22, one hour at -0.16) and checks the note is present there and absent on a
day whose prices are all > 0. Run: python3 -m unittest tests.test_zeroneg_build"""
import datetime as dt
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import build  # noqa: E402

SYN = dt.date(2026, 11, 22)
POS = dt.date(2026, 11, 21)


def synthetic(day: dt.date) -> str:
    rows = [f"{day.year};{day.month:02d};{day.day:02d};{p};{-0.16 if 49 <= p <= 52 else 55.5};55.5;" for p in range(1, 97)]
    return "MARGINALPDBCPT;\n" + "\n".join(rows) + "\n*\n"


class ZeroNegBuild(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmpd = tempfile.TemporaryDirectory()
        tmp = Path(cls.tmpd.name)
        shutil.copytree(ROOT / "data" / "omie", tmp / "omie")
        (tmp / "omie" / f"{SYN}.txt").write_text(synthetic(SYN), encoding="latin-1")
        now = dt.datetime(2026, 11, 21, 13, 15, tzinfo=dt.timezone.utc)
        with mock.patch.object(build, "DATA_DIR", tmp / "omie"), mock.patch.object(build, "PUBLIC_DIR", tmp / "public"), \
                mock.patch.object(build, "ROOT", tmp), mock.patch("urllib.request.urlopen", side_effect=OSError("offline")):
            build.build(SYN, offline=True, now=now)
        cls.pub = tmp / "public"
        build.STAMPS.clear()

    @classmethod
    def tearDownClass(cls):
        cls.tmpd.cleanup()

    def page(self, d):
        return (self.pub / "d" / str(d) / "index.html").read_text(encoding="utf-8")

    def test_note_on_negative_day(self):
        h = self.page(SYN)
        self.assertIn("Preço zero ou negativo é real", h)
        self.assertIn("zero or negative price is real", h)
        self.assertIn("marginalpdbcpt_20261122.1", h)
        self.assertEqual(h.count("<h1"), 1)
        self.assertNotRegex(h, r"<script(?![^>]*application/ld\+json)")

    def test_no_note_on_positive_day(self):
        h = self.page(POS)
        self.assertNotIn("Preço zero ou negativo é real", h)
        self.assertNotIn("zero or negative price is real", h)


if __name__ == "__main__":
    unittest.main()
