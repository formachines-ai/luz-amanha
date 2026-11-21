"""T-0231: cached OMIE files < 48 h old are re-fetched and replaced when OMIE republished different content; older files
are never re-fetched; a failed download keeps the cache. HTTP is mocked, temp cache/public dirs.
Run: python3 -m unittest tests.test_refetch"""
import datetime as dt
import io
import json
import shutil
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import build  # noqa: E402

UTC = dt.timezone.utc


class Resp(io.BytesIO):
    status = 200
    headers = {"Content-Type": "application/octet-stream"}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class Refetch(unittest.TestCase):
    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.tmp = Path(self._t.name)
        shutil.copytree(ROOT / "data" / "omie", self.tmp / "omie")
        days = sorted(p.stem for p in (self.tmp / "omie").glob("????-??-??.txt"))
        self.day = dt.date.fromisoformat(days[-1])
        self.cache = self.tmp / "omie" / f"{self.day}.txt"
        self.old = self.cache.read_bytes()
        lines = self.old.decode("latin-1").split("\n")
        i = next(k for k, ln in enumerate(lines) if ln.count(";") >= 6 and ln[:4].isdigit())
        parts = lines[i].split(";")
        parts[4] = "999.99"  # Portuguese price of the first quarter
        lines[i] = ";".join(parts)
        self.new = "\n".join(lines).encode("latin-1")
        self.assertNotEqual(self.old, self.new)
        self.patches = [mock.patch.object(build, "DATA_DIR", self.tmp / "omie"), mock.patch.object(build, "PUBLIC_DIR", self.tmp / "public"),
                        mock.patch.object(build, "ROOT", self.tmp)]
        for p in self.patches:
            p.start()
        self.calls = []

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self._t.cleanup()

    def serve(self, body=None, exc=None):
        def fake(req, timeout=None):
            self.calls.append(req.full_url)
            if f"{self.day:%Y%m%d}" not in req.full_url:  # every other day: not republished / not published
                raise urllib.error.HTTPError(req.full_url, 404, "nf", {}, io.BytesIO(b"<html></html>"))
            if exc:
                raise exc
            return Resp(body)
        return mock.patch("urllib.request.urlopen", side_effect=fake)

    def stamp(self, first: dt.datetime):
        (self.tmp / "omie" / "stamps.json").write_text(json.dumps({self.day.isoformat(): {"sha256": "x", "changed_utc": first.isoformat()}}))

    def now(self, hours_after_day_start):
        return dt.datetime.combine(self.day, dt.time(0), tzinfo=UTC) + dt.timedelta(hours=hours_after_day_start)

    def prices(self):
        parsed = build.parse_omie(self.cache.read_text(encoding="latin-1"))
        return [r for r in build.period_starts(parsed)]

    def test_young_file_replaced_and_prices_change(self):
        now = self.now(10)
        self.stamp(now - dt.timedelta(hours=5))
        before = self.prices()
        with self.serve(self.new):
            text = build.fetch_day(self.day, now=now)
        self.assertTrue(self.calls)
        self.assertEqual(self.cache.read_bytes(), self.new)
        self.assertEqual(text, self.new.decode("latin-1"))
        self.assertNotEqual(before, self.prices())
        # the built hour prices and the page date follow
        with self.serve(self.new):
            build.build(self.day, offline=False, now=now)
        prev = self.day - dt.timedelta(days=1)  # OMIE period 1 of D is the Lisbon hour 23:00 of D-1
        j = json.loads((self.tmp / "public" / "d" / prev.isoformat() / "index.json").read_text(encoding="utf-8"))
        self.assertIn(999.99, [q for h in j["hours"] for q in (h.get("quarters_eur_mwh") or [])])
        stamps = json.loads((self.tmp / "omie" / "stamps.json").read_text())
        self.assertEqual(stamps[self.day.isoformat()]["changed_utc"], now.isoformat(timespec="seconds"))

    def test_unchanged_upstream_keeps_stamp(self):
        now = self.now(10)
        first = now - dt.timedelta(hours=5)
        self.stamp(first)
        with self.serve(self.old):
            build.fetch_day(self.day, now=now)
        self.assertTrue(self.calls)
        self.assertEqual(self.cache.read_bytes(), self.old)

    def test_old_file_not_refetched(self):
        now = self.now(24 * 5)
        self.stamp(now - dt.timedelta(days=4))
        with self.serve(self.new):
            build.fetch_day(self.day, now=now)
        self.assertEqual(self.calls, [])
        self.assertEqual(self.cache.read_bytes(), self.old)

    def test_failed_download_keeps_cache_and_build_succeeds(self):
        now = self.now(10)
        self.stamp(now - dt.timedelta(hours=5))
        for exc in (urllib.error.URLError("boom"), TimeoutError("slow"),
                    urllib.error.HTTPError("u", 500, "err", {}, io.BytesIO(b"x"))):
            self.calls.clear()
            with self.serve(exc=exc):
                text = build.fetch_day(self.day, now=now)
            self.assertTrue(self.calls)
            self.assertEqual(text, self.old.decode("latin-1").replace("\r\n", "\n"))
            self.assertEqual(self.cache.read_bytes(), self.old)
        with self.serve(exc=TimeoutError("slow")):
            self.assertIn(build.build(self.day, offline=False, now=now), (0, 2))
        self.assertEqual(self.cache.read_bytes(), self.old)

    def test_html_body_keeps_cache(self):
        now = self.now(10)
        self.stamp(now - dt.timedelta(hours=1))
        with self.serve(b"<html>not found</html>"):
            build.fetch_day(self.day, now=now)
        self.assertEqual(self.cache.read_bytes(), self.old)


if __name__ == "__main__":
    unittest.main()
