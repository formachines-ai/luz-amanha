"""month_to_date equals mean of the hourly prices of the built dated pages (T-0222, T-0223).
Builds offline from the cached data/omie/ into a temp dir (never reads or writes public/ or data/).
Run: python3 -m unittest tests.test_month_to_date"""
import datetime as dt
import decimal
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import build  # noqa: E402

TMP = None
OUT = None


def setUpModule():
    global TMP, OUT
    TMP = tempfile.TemporaryDirectory()
    tmp = Path(TMP.name)
    shutil.copytree(ROOT / "data" / "omie", tmp / "omie")
    days = sorted(p.stem for p in (tmp / "omie").glob("????-??-??.txt"))
    target = dt.date.fromisoformat(days[-1])
    with mock.patch.object(build, "DATA_DIR", tmp / "omie"), mock.patch.object(build, "PUBLIC_DIR", tmp / "public"), \
            mock.patch.object(build, "ROOT", tmp), mock.patch("urllib.request.urlopen", side_effect=OSError("network disabled")):
        build.build(target, offline=True)
    OUT = tmp / "public"


def tearDownModule():
    TMP.cleanup()


class MonthToDate(unittest.TestCase):
    def test_matches_dated_pages(self):
        m = json.loads((OUT / "today.json").read_text(encoding="utf-8"))["month_to_date"]
        vals = []
        for f in sorted((OUT / "d").glob(m["month"] + "-*/index.json")):
            if m["from"] <= f.parent.name <= m["to"]:
                j = json.loads(f.read_text(encoding="utf-8"))
                vals += [h["price_pt_eur_mwh"] for h in j.get("hours", [])]
        vals = [v for v in vals if v is not None]
        self.assertTrue(vals)
        self.assertEqual(len(vals), m["hours"])
        self.assertAlmostEqual(sum(vals) / len(vals), m["avg_eur_mwh"], places=2)
        exp = decimal.Decimal(repr(m["avg_eur_mwh"])) / 10
        exp = float(exp.quantize(decimal.Decimal("0.1"), rounding=decimal.ROUND_HALF_UP))
        self.assertEqual(m["avg_cent_kwh"], exp)
        self.assertEqual(m["avg_pt_eur_mwh"], m["avg_eur_mwh"])
        self.assertEqual(m["avg_pt_cent_kwh"], m["avg_cent_kwh"])


if __name__ == "__main__":
    unittest.main()
