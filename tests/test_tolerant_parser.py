"""T-0253: tolerant OMIE parser. CRLF, UTF-8 BOM, Latin-1 header bytes, blank lines, '*' trailer, spaces, decimal comma give the same
24 hourly prices as the clean LF file; an empty/non-numeric Portugal quarter makes that hour null (no 3-quarter mean, no NaN) and the
build completes; a non-OMIE body is invalid_body and the cache is kept. Temp dirs only.
Run: python3 -m unittest tests.test_tolerant_parser"""
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
from tests._omie import SNAPSHOT, copy_omie  # noqa: E402

D = dt.date(2026, 11, 22)


def price(p):
    return 40 + p * 0.5 - (100 if 9 <= p <= 12 else 0)   # includes one negative price


def rows(fmt=lambda p: f"{price(p):.2f}", pt_override=None):
    out = []
    for p in range(1, 97):
        v = pt_override(p) if pt_override and pt_override(p) is not None else fmt(p)
        out.append(f"{D.year};{D.month:02d};{D.day:02d};{p};{v};{price(p):.2f};")
    return out


def hours(text):
    files = {D: build.parse_omie(text)}
    return [h["pt"] for h in build.lisbon_hours(D, files)[:22]]   # D+1 file absent: hours 0..22 are known


class Variants(unittest.TestCase):
    clean = "MARGINALPDBCPT;\n" + "\n".join(rows()) + "\n*\n"

    def test_same_prices(self):
        base = hours(self.clean)
        self.assertEqual(len([x for x in base if x is not None]), 22)
        self.assertTrue(any(x < 0 for x in base))
        crlf = self.clean.replace("\n", "\r\n")
        bom_u = "﻿" + self.clean
        bom_l1 = "\xef\xbb\xbf" + self.clean            # BOM bytes decoded as Latin-1
        latin = self.clean.replace("MARGINALPDBCPT;", "MARGINALPDBCPT;Preço médio ç;")  # non-ASCII header byte
        blank = "\n\n" + "\n\n".join(self.clean.split("\n")) + "\n\n"
        spaces = "MARGINALPDBCPT;\n" + "\n".join(" " + r.replace(";", " ; ").rstrip() for r in rows()) + "\n *\n"
        comma = "MARGINALPDBCPT;\n" + "\n".join(rows(lambda p: f"{price(p):.2f}".replace(".", ","))) + "\n*\n"
        for name, body in dict(crlf=crlf, bom_u=bom_u, bom_l1=bom_l1, latin=latin, blank=blank, spaces=spaces, comma=comma).items():
            with self.subTest(name):
                self.assertTrue(build.looks_like_omie(body))
                self.assertEqual(hours(body), base)

    def test_latin1_bytes_roundtrip(self):
        body = ("\xef\xbb\xbfMARGINALPDBCPT;\r\n" + "\r\n".join(rows()) + "\r\n*\r\n").encode("latin-1")
        self.assertEqual(hours(body.decode("latin-1")), hours(self.clean))

    def test_empty_quarter_is_null(self):
        base = hours(self.clean)
        for bad in ("", "n/a", "NaN"):
            body = "MARGINALPDBCPT;\n" + "\n".join(rows(pt_override=lambda p: bad if p == 14 and bad else (" " if p == 14 else None))) + "\n*\n"
            hh = hours(body)
            # quarter 14 = CET hour 3 = Lisbon hour 02
            self.assertIsNone(hh[2], bad)
            self.assertEqual(hh[:2] + hh[3:], base[:2] + base[3:])

    def test_build_completes_with_gap(self):
        body = "MARGINALPDBCPT;\n" + "\n".join(rows(pt_override=lambda p: "" if p == 14 else None)) + "\n*\n"
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t)
            copy_omie(tmp / "omie")
            (tmp / "omie" / f"{D}.txt").write_text(body, encoding="latin-1")
            nxt = D + dt.timedelta(days=1)   # D+1 file supplies Lisbon hour 23
            (tmp / "omie" / f"{nxt}.txt").write_text("MARGINALPDBCPT;\n" + "\n".join(r.replace(f";{D.day:02d};", f";{nxt.day:02d};") for r in rows()) + "\n*\n", encoding="latin-1")
            now = dt.datetime(2026, 11, 21, 13, 15, tzinfo=dt.timezone.utc)
            with mock.patch.object(build, "DATA_DIR", tmp / "omie"), mock.patch.object(build, "PUBLIC_DIR", tmp / "public"), \
                    mock.patch.object(build, "ROOT", tmp), mock.patch("urllib.request.urlopen", side_effect=OSError("x")):
                build.build(D, offline=True, now=now)
            build.STAMPS.clear()
            j = json.loads((tmp / "public" / "d" / str(D) / "index.json").read_text())
            pr = [h["price_pt_eur_mwh"] for h in j["hours"]]
            self.assertIsNone(pr[2])
            self.assertEqual(sum(1 for x in pr if x is None), 1)
            self.assertEqual(j["stats"]["available"], 23)
            self.assertTrue(all(x is None or x == x for x in pr))
            self.assertTrue(any(x is not None and x < 0 for x in pr))


class Resp(io.BytesIO):
    status = 200
    headers = {"Content-Type": "text/plain"}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class InvalidBody(unittest.TestCase):
    def test_non_omie_keeps_cache(self):
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t)
            (tmp / "omie").mkdir()
            cache = tmp / "omie" / f"{D}.txt"
            good = "MARGINALPDBCPT;\n" + "\n".join(rows()) + "\n*\n"
            cache.write_text(good, encoding="latin-1")
            build.REFETCH.update(checked=0, republished=[], failed=0, failed_detail=[])
            now = dt.datetime(2026, 11, 22, 10, 0, tzinfo=dt.timezone.utc)
            with mock.patch.object(build, "DATA_DIR", tmp / "omie"), mock.patch("urllib.request.urlopen", side_effect=lambda *a, **k: Resp(b"<html>oops</html>")):
                got = build.fetch_day(D, now=now)
            self.assertEqual(got, good)
            self.assertEqual(cache.read_text(encoding="latin-1"), good)
            self.assertTrue(build.REFETCH["failed_detail"])
            self.assertTrue(all(e["reason"] == "invalid_body" for e in build.REFETCH["failed_detail"]))
            self.assertFalse(build.looks_like_omie("<html>x</html>"))


if __name__ == "__main__":
    unittest.main()
