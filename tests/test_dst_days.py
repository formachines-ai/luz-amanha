"""T-0245: DST days get the right start_local/start_utc and hour means. Synthetic OMIE files D-1, D, D+1 (temp dir, offline build)
for the 25-hour Lisbon day 2026-10-25 (100 quarters) and the 23-hour day 2027-03-28 (92 quarters).
Run: python3 -m unittest tests.test_dst_days"""
import datetime as dt
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import build  # noqa: E402

UTC = dt.timezone.utc
EPOCH = dt.datetime(2026, 1, 1, tzinfo=UTC)


def quarter_price(start_utc):
    """Unique price per real quarter: its index in 15-min steps since EPOCH (so the hour mean is checkable)."""
    return float(int((start_utc - EPOCH) / dt.timedelta(minutes=15)))


def synth(day):
    start = dt.datetime.combine(day, dt.time(0), tzinfo=ZoneInfo("Europe/Madrid")).astimezone(UTC)
    end = dt.datetime.combine(day + dt.timedelta(days=1), dt.time(0), tzinfo=ZoneInfo("Europe/Madrid")).astimezone(UTC)
    n = int((end - start) / dt.timedelta(minutes=15))
    rows = [f"{day.year};{day.month:02d};{day.day:02d};{k};{quarter_price(start + dt.timedelta(minutes=15 * (k - 1)))};1.0;"
            for k in range(1, n + 1)]
    return n, "MARGINALPDBCPT;\n" + "\n".join(rows) + "\n*\n"


def build_day(tmp, day):
    omie = tmp / "omie"
    omie.mkdir()
    counts = {}
    for d in (day - dt.timedelta(days=1), day, day + dt.timedelta(days=1)):
        counts[d], text = synth(d)
        (omie / f"{d}.txt").write_text(text, encoding="latin-1")
    now = dt.datetime.combine(day - dt.timedelta(days=1), dt.time(13, 15), tzinfo=UTC)
    with mock.patch.object(build, "DATA_DIR", omie), mock.patch.object(build, "PUBLIC_DIR", tmp / "public"), \
            mock.patch.object(build, "ROOT", tmp), mock.patch("urllib.request.urlopen", side_effect=OSError("x")):
        rc = build.build(day, offline=True, now=now)
    build.STAMPS.clear()
    j = json.loads((tmp / "public" / "d" / str(day) / "index.json").read_text())
    return rc, counts[day], j


class Dst(unittest.TestCase):
    def check_common(self, rc, nq, j, nhours, locals_):
        self.assertEqual(rc, 0)
        hs = j["hours"]
        self.assertEqual(len(hs), nhours)
        self.assertEqual([h["start_local"][11:] for h in hs[:4]], locals_[:4])
        utc = [dt.datetime.strptime(h["start_utc"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC) for h in hs]
        for a, b in zip(utc, utc[1:]):
            self.assertEqual(b - a, dt.timedelta(hours=1))
        for h, u in zip(hs, utc):
            self.assertEqual(h["start_local"], u.astimezone(ZoneInfo("Europe/Lisbon")).isoformat())
            qs = [quarter_price(u + dt.timedelta(minutes=15 * i)) for i in range(4)]
            self.assertEqual(h["quarters_eur_mwh"], qs)
            self.assertAlmostEqual(h["price_pt_eur_mwh"], round(sum(qs) / 4, 2), places=2)
        by_utc = [h["start_utc"] for h in hs]
        for key in ("cheap_window", "dear_window"):
            w = j["stats"][key]
            self.assertEqual(w["end"] - w["start"], 3)
            for a, b in zip(utc[w["start"]:w["end"]], utc[w["start"] + 1:w["end"]]):
                self.assertEqual(b - a, dt.timedelta(hours=1))
        self.assertLessEqual(j["cheap_window"]["avg_eur_mwh"], j["dear_window"]["avg_eur_mwh"])
        self.assertTrue(by_utc)

    def test_25_hour_day(self):
        with tempfile.TemporaryDirectory() as t:
            rc, nq, j = build_day(Path(t), dt.date(2026, 10, 25))
        self.assertEqual(nq, 100)
        exp = ["00:00:00+01:00", "01:00:00+01:00", "01:00:00+00:00", "02:00:00+00:00"]
        self.check_common(rc, nq, j, 25, exp)
        self.assertEqual(j["hours"][-1]["start_local"][11:], "23:00:00+00:00")
        self.assertEqual([h["start_local"][11:] for h in j["hours"][4:]], [f"{i:02d}:00:00+00:00" for i in range(3, 24)])

    def test_23_hour_day(self):
        with tempfile.TemporaryDirectory() as t:
            rc, nq, j = build_day(Path(t), dt.date(2027, 3, 28))
        self.assertEqual(nq, 92)
        exp = ["00:00:00+00:00", "02:00:00+01:00", "03:00:00+01:00", "04:00:00+01:00"]
        self.check_common(rc, nq, j, 23, exp)
        self.assertEqual(j["hours"][-1]["start_local"][11:], "23:00:00+01:00")


if __name__ == "__main__":
    unittest.main()
