"""Post-build QA checklist (T-0194, VISION "QA после сборки").
Builds offline from the cached data/omie/ into a temp dir (never public/ or data/).
Run: python3 -m unittest tests.test_qa_build"""
import datetime as dt
import json
import math
import re
import shutil
import sys
import tempfile
import unittest
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import build  # noqa: E402
from tests._omie import SNAPSHOT, copy_omie  # noqa: E402

TMP = None
OUT = None


def setUpModule():
    global TMP, OUT
    TMP = tempfile.TemporaryDirectory()
    tmp = Path(TMP.name)
    copy_omie(tmp / "omie")
    days = sorted(p.stem for p in (tmp / "omie").glob("????-??-??.txt"))
    target = dt.date.fromisoformat(days[-1])
    with mock.patch.object(build, "DATA_DIR", tmp / "omie"), mock.patch.object(build, "PUBLIC_DIR", tmp / "public"), \
            mock.patch.object(build, "ROOT", tmp), mock.patch("urllib.request.urlopen", side_effect=OSError("network disabled")):
        build.build(target, offline=True)
    OUT = tmp / "public"


def tearDownModule():
    TMP.cleanup()


def day_jsons():
    return [(p, json.loads(p.read_text(encoding="utf-8"))) for p in sorted(OUT.glob("d/????-??-??/index.json"))]


class QA(unittest.TestCase):
    def test_1_html_pages(self):
        pages = sorted(OUT.rglob("*.html"))
        self.assertGreater(len(pages), 5)
        for p in pages:
            h = p.read_text(encoding="utf-8")
            name = str(p.relative_to(OUT))
            if name != "404.html" and not name.startswith("go/"):
                self.assertEqual(len(re.findall(r"<h1[\s>]", h)), 1, name)
            self.assertIn("Feito por agentes de IA", h, name)
            self.assertIn("Made by AI agents", h, name)
            for m in re.finditer(r"<script\b([^>]*)>", h):
                self.assertIn("application/ld+json", m.group(1), name)

    def test_2_hours(self):
        days = day_jsons()
        self.assertTrue(days)
        n_avail = 0
        for p, d in days:
            if not d.get("available"):
                continue
            n_avail += 1
            hrs = d["hours"]
            self.assertIn(len(hrs), (23, 24, 25), p)
            vals = []
            for h in hrs:
                v = h["price_pt_eur_mwh"]
                if v is not None:
                    self.assertFalse(math.isnan(v), p)
                    vals.append(v)
            self.assertEqual(d["stats"]["available"], len(vals), p)
        self.assertGreater(n_avail, 0)
        t = json.loads((OUT / "tomorrow.json").read_text(encoding="utf-8"))
        self.assertIn("available", t)

    def test_3_windows(self):
        for p, d in day_jsons():
            if not d.get("available"):
                continue
            s = d["stats"]
            if s.get("cheap_window") and s.get("dear_window"):
                self.assertLessEqual(s["cheap_window"]["avg"], s["avg"] + 0.005, p)
                self.assertLessEqual(s["avg"], s["dear_window"]["avg"] + 0.005, p)

    def test_4_rounding_and_quarters(self):
        for p, d in day_jsons():
            if not d.get("available"):
                continue
            for h in d["hours"]:
                v = h["price_pt_eur_mwh"]
                self.assertIn("quarters_eur_mwh", h, (p, h["hour"]))
                if v is None:
                    self.assertIsNone(h["quarters_eur_mwh"], (p, h["hour"]))
                    continue
                exp = float((Decimal(repr(float(v))) / 10).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)) + 0.0
                self.assertEqual(h["price_pt_cent_kwh"], exp, (p, h["hour"]))
                q = h.get("quarters_eur_mwh")
                self.assertEqual(q, h.get("quarters_pt_eur_mwh"), (p, h["hour"]))
                if q:
                    self.assertEqual(len(q), 4, (p, h["hour"]))
                    self.assertEqual(round(sum(q) / 4, 2), v, (p, h["hour"]))
                    self.assertAlmostEqual(sum(q) / len(q), v, delta=0.011, msg=(p, h["hour"]))

    def test_5_index_pt_en_windows(self):
        h = (OUT / "index.html").read_text(encoding="utf-8")
        pt = h.split('id="en"')[0]
        en = h[len(pt):]
        pat = re.compile(r'<dd class="g">(\d\d:\d\d–\d\d:\d\d) · (-?[\d,.]+) c/kWh <small class="mwh">\((-?[\d.]+) €/MWh\)</small></dd>'
                         r'.*?<dd class="r">(\d\d:\d\d–\d\d:\d\d) · (-?[\d,.]+) c/kWh <small class="mwh">\((-?[\d.]+) €/MWh\)</small></dd>', re.S)
        a, b = pat.search(pt), pat.search(en)
        self.assertTrue(a and b, "window blocks not found in PT and EN")
        na = [x.replace(",", ".") for x in a.groups()]
        nb = [x.replace(",", ".") for x in b.groups()]
        self.assertEqual(na, nb)


if __name__ == "__main__":
    unittest.main()
