"""T-0244: slow_window = cheapest 6 consecutive priced hours; absent with fewer than 6 priced hours. Temp-dir offline build
of synthetic days. Run: python3 -m unittest tests.test_slow_window"""
import datetime as dt
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
from tests._omie import SNAPSHOT, copy_omie  # noqa: E402

D1 = dt.date(2026, 11, 22)   # full day, cheap run planted
D2 = dt.date(2026, 11, 23)   # tomorrow: only first 5 hours priced? (file holds 96 rows; hour 23 needs D+1)


def synth(day, price):
    rows = [f"{day.year};{day.month:02d};{day.day:02d};{p};{price(p)};{price(p)};" for p in range(1, 97)]
    return "MARGINALPDBCPT;\n" + "\n".join(rows) + "\n*\n"


class Slow(unittest.TestCase):
    def test_min_run_and_page(self):
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t)
            copy_omie(tmp / "omie")
            # Madrid hours 3..8 (quarters 9..32) cheap at 10, rest rising
            (tmp / "omie" / f"{D1}.txt").write_text(synth(D1, lambda p: 10 if 9 <= p <= 32 else 50 + p), encoding="latin-1")
            now = dt.datetime(2026, 11, 21, 13, 15, tzinfo=dt.timezone.utc)
            with mock.patch.object(build, "DATA_DIR", tmp / "omie"), mock.patch.object(build, "PUBLIC_DIR", tmp / "public"), \
                    mock.patch.object(build, "ROOT", tmp), mock.patch("urllib.request.urlopen", side_effect=OSError("x")):
                build.build(D1, offline=True, now=now)
            build.STAMPS.clear()
            j = json.loads((tmp / "public" / "d" / str(D1) / "index.json").read_text())
            sw = j["slow_window"]
            self.assertEqual(sw["hours"], 6)
            pr = [h["price_pt_eur_mwh"] for h in j["hours"]]
            best = min(sum(pr[s:s + 6]) / 6 for s in range(len(pr) - 5) if None not in pr[s:s + 6])
            self.assertAlmostEqual(sw["avg_eur_mwh"], best, places=2)
            i = [h["start_utc"] for h in j["hours"]].index(sw["start_utc"])
            self.assertAlmostEqual(sum(pr[i:i + 6]) / 6, sw["avg_eur_mwh"], places=2)
            h = (tmp / "public" / "d" / str(D1) / "index.html").read_text()
            self.assertIn("Carga lenta", h)
            self.assertIn("Slow charge", h)

    def test_fewer_than_six(self):
        hrs = [{"label": f"{i:02d}:00", "pt": (5.0 if i < 5 else None)} for i in range(24)]
        self.assertIsNone(build.slow_window(hrs))
        self.assertEqual(build.slow_line(hrs, "pt"), "")
        self.assertEqual(build._slow_json(hrs), {})
        hrs[5]["pt"] = 1.0
        self.assertEqual(build.slow_window(hrs)["start"], 0)
        hrs[3]["pt"] = None
        self.assertIsNone(build.slow_window(hrs))


if __name__ == "__main__":
    unittest.main()
