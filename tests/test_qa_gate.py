"""T-0239: post-build QA gate. Builds offline into a temp dir (copy of data/omie/, empty public/); synthetic bad days
must fail QA, exit non-zero and leave the old output unchanged. Run: python3 -m unittest tests.test_qa_gate"""
import datetime as dt
import hashlib
import json
import math
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

NOW = dt.datetime(2026, 11, 21, 13, 15, tzinfo=dt.timezone.utc)
TARGET = dt.date(2026, 11, 22)


def tree(p: Path) -> dict:
    return {str(f.relative_to(p)): hashlib.sha256(f.read_bytes()).hexdigest() for f in sorted(p.rglob("*")) if f.is_file()}


def run(tmp: Path, patch=None) -> int:
    ctx = [mock.patch.object(build, "DATA_DIR", tmp / "omie"), mock.patch.object(build, "PUBLIC_DIR", tmp / "public"),
           mock.patch.object(build, "ROOT", tmp), mock.patch("urllib.request.urlopen", side_effect=OSError("offline"))]
    if patch:
        ctx.append(patch)
    for c in ctx:
        c.start()
    try:
        return build.build(TARGET, offline=True, now=NOW)
    finally:
        for c in reversed(ctx):
            c.stop()
        build.STAMPS.clear()


def mutate(fn):
    orig = build.day_json

    def wrapped(day, *a, **k):
        j = orig(day, *a, **k)
        if day == TARGET:
            fn(j)
        return j
    return mock.patch.object(build, "day_json", wrapped)


def nan_hour(j):
    j["hours"][3]["price_pt_eur_mwh"] = math.nan


def wrong_date(j):
    j["date"] = "2026-11-23"


def cheap_above_avg(j):
    j["stats"]["cheap_window"]["avg"] = j["stats"]["avg"] + 10


class QAGate(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.TemporaryDirectory()
        self.tmp = Path(self.d.name)
        copy_omie(self.tmp / "omie")

    def tearDown(self):
        self.d.cleanup()

    def test_good_day_passes_and_reports(self):
        self.assertIn(run(self.tmp), (0, 2))
        qa = json.loads((self.tmp / "public" / "build.json").read_text(encoding="utf-8"))["qa"]
        self.assertIs(qa["passed"], True)
        self.assertEqual(len(qa["checks"]), 6)
        self.assertTrue(all(c["ok"] for c in qa["checks"]))
        # 23-value day (2026-11-22, file D+1 not out) and a negative-price day pass
        j = json.loads((self.tmp / "public" / "d" / "2026-11-22" / "index.json").read_text(encoding="utf-8"))
        self.assertEqual(j["stats"]["available"], 23)
        self.assertFalse((self.tmp / "public.staging").exists())
        self.assertFalse((self.tmp / "data" / "qa-failures").exists())

    def test_bad_days_fail_and_keep_old_output(self):
        self.assertIn(run(self.tmp), (0, 2))
        before = tree(self.tmp / "public")
        for name, fn, check in (("nan", nan_hour, "no NaN"), ("date", wrong_date, "date"), ("order", cheap_above_avg, "cheap <= avg <= dear")):
            with self.subTest(name):
                with self.assertLogs(build.log, "ERROR") as cm:
                    rc = run(self.tmp, mutate(fn))
                self.assertEqual(rc, 3)
                self.assertTrue(any(check in m for m in cm.output), cm.output)
                self.assertEqual(tree(self.tmp / "public"), before)
                self.assertFalse((self.tmp / "public.staging").exists())
                rec = json.loads((self.tmp / "data" / "qa-failures" / "2026-11-21.json").read_text(encoding="utf-8"))
                self.assertEqual(rec["date_checked"], "2026-11-21")
                self.assertTrue(rec["built_utc"].endswith("Z"))
                self.assertTrue(any(check in f["name"] for f in rec["failed"]), rec)
                shutil.rmtree(self.tmp / "data")


if __name__ == "__main__":
    unittest.main()
