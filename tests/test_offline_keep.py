"""Offline build keeps an available published day when the cache lacks its file (T-0096)."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_offline_build import run_build  # noqa: E402


class OfflineKeep(unittest.TestCase):
    def test_offline_keeps_available_tomorrow_without_cache(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            online = run_build(tmp, offline=False, network=True)
            index_before = (tmp / "public" / "index.html").read_text(encoding="utf-8")
            (tmp / "omie" / "2026-11-03.txt").unlink()  # checkout without the newest cached file
            off = run_build(tmp, offline=True, network=False)
            self.assertTrue(off["available"])
            self.assertEqual(off["date"], "2026-11-03")
            self.assertEqual(off["hours"], online["hours"])
            self.assertEqual((tmp / "public" / "index.html").read_text(encoding="utf-8"), index_before)

    def test_online_build_without_file_is_unavailable(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            run_build(tmp, offline=False, network=True)
            (tmp / "omie" / "2026-11-03.txt").unlink()
            on = run_build(tmp, offline=False, network=False)
            self.assertFalse(on["available"])

    def test_no_existing_json_stays_unavailable(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertFalse(run_build(Path(d), offline=True, network=False)["available"])

    def test_other_date_is_not_kept(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            run_build(tmp, offline=False, network=True)
            (tmp / "omie" / "2026-11-03.txt").unlink()
            p = tmp / "public" / "tomorrow.json"
            j = json.loads(p.read_text(encoding="utf-8"))
            j["date"] = "2026-11-02"
            p.write_text(json.dumps(j), encoding="utf-8")
            (tmp / "public" / "d" / "2026-11-03" / "index.json").unlink()
            self.assertFalse(run_build(tmp, offline=True, network=False)["available"])


if __name__ == "__main__":
    unittest.main()
