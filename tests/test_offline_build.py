"""Offline rebuild keeps tomorrow's data (T-0042). Run: python3 -m unittest tests.test_offline_build"""
import datetime as dt
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import build  # noqa: E402

FIXTURE = ROOT / "data" / "fixtures" / "marginalpdbcpt_20261103.1"
TARGET = dt.date(2026, 11, 3)


class FakeResp(io.BytesIO):
    status = 200
    headers = {"Content-Type": "application/octet-stream"}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def run_build(tmp: Path, offline: bool, network: bool) -> dict:
    def fake_urlopen(req, timeout=None):
        if network and req.full_url.endswith("marginalpdbcpt_20261103.1"):
            return FakeResp(FIXTURE.read_bytes())
        raise OSError("network disabled")

    with mock.patch.object(build, "DATA_DIR", tmp / "omie"), mock.patch.object(build, "PUBLIC_DIR", tmp / "public"), mock.patch.object(build, "ROOT", tmp), \
            mock.patch("urllib.request.urlopen", fake_urlopen):
        build.build(TARGET, offline=offline)
    return json.loads((tmp / "public" / "tomorrow.json").read_text(encoding="utf-8"))


class OfflineBuild(unittest.TestCase):
    def test_cached_tomorrow_survives_offline_rebuild(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            online = run_build(tmp, offline=False, network=True)
            self.assertTrue(online["available"])
            self.assertTrue((tmp / "omie" / "2026-11-03.txt").exists())
            offline = run_build(tmp, offline=True, network=False)
            self.assertTrue(offline["available"])
            self.assertEqual(offline["date"], "2026-11-03")
            self.assertEqual(offline["hours"], online["hours"])
            self.assertTrue(len(offline["hours"]) >= 23)
            index = (tmp / "public" / "index.html").read_text(encoding="utf-8")
            self.assertIn("03.11", index)
            self.assertTrue((tmp / "public" / "d" / "2026-11-03" / "index.html").exists())

    def test_failed_download_uses_cache(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            online = run_build(tmp, offline=False, network=True)
            again = run_build(tmp, offline=False, network=False)  # network error, cache present
            self.assertTrue(again["available"])
            self.assertEqual(again["hours"], online["hours"])

    def test_no_cache_means_unavailable(self):
        with tempfile.TemporaryDirectory() as d:
            tomorrow = run_build(Path(d), offline=True, network=False)
            self.assertIs(tomorrow["available"], False)
            self.assertNotIn("hours", tomorrow)


if __name__ == "__main__":
    unittest.main()
