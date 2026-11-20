"""T-0216: the 23:00 note appears iff D+1 is not cached. Run: python3 -m unittest tests.test_hour23_note"""
import re
import tempfile
import unittest
from pathlib import Path

from tests.test_offline_build import FIXTURE, TARGET, build, json, mock

ROOT = Path(__file__).resolve().parent.parent
PT = "ficheiro OMIE do dia seguinte"
EN = "next day's OMIE file"


def build_with(tmp: Path, with_next: bool) -> dict:
    (tmp / "omie").mkdir()
    raw = FIXTURE.read_bytes()
    (tmp / "omie" / "2026-11-03.txt").write_bytes(raw)
    if with_next:
        nxt = re.sub(rb"2026;11;03;", b"2026;11;04;", raw).replace(b"20261103", b"20261104")
        (tmp / "omie" / "2026-11-04.txt").write_bytes(nxt)
    with mock.patch.object(build, "DATA_DIR", tmp / "omie"), mock.patch.object(build, "PUBLIC_DIR", tmp / "public"), \
            mock.patch.object(build, "ROOT", tmp):
        build.build(TARGET, offline=True)
    return json.loads((tmp / "public" / "tomorrow.json").read_text(encoding="utf-8"))


class Hour23Note(unittest.TestCase):
    def test_note_present_without_next_day(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            j = build_with(tmp, False)
            self.assertIsNone(j["hours"][23]["price_pt_eur_mwh"])
            self.assertIn(PT, j["hour_23_note"]["pt"])
            self.assertIn(EN, j["hour_23_note"]["en"])
            for page in (tmp / "public" / "index.html", tmp / "public" / "d" / "2026-11-03" / "index.html"):
                html = page.read_text(encoding="utf-8")
                self.assertEqual(html.count(PT), 1, page)
                self.assertIn("next day&#x27;s OMIE file", html)

    def test_note_absent_with_next_day(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            j = build_with(tmp, True)
            self.assertIsNotNone(j["hours"][23]["price_pt_eur_mwh"])
            self.assertIsNone(j["hour_23_note"])
            self.assertNotIn(PT, (tmp / "public" / "index.html").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
