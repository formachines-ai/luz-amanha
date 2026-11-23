"""T-0260: /d/ archive links every dated page; each dated page links previous/next existing day only; footer links /d/.
Temp-dir offline build, synthetic 3-day set. Run: python3 -m unittest tests.test_day_links"""
import datetime as dt
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import build  # noqa: E402

DAYS = [dt.date(2026, 11, 20), dt.date(2026, 11, 21), dt.date(2026, 11, 22)]


def synth(day):
    rows = [f"{day.year};{day.month:02d};{day.day:02d};{p};{50 + p};{50 + p};" for p in range(1, 97)]
    return "MARGINALPDBCPT;\n" + "\n".join(rows) + "\n*\n"


def hrefs(path):
    return re.findall(r'href="(/d/[^"]*)"', path.read_text(encoding="utf-8"))


class DayLinks(unittest.TestCase):
    def test_archive_prev_next_footer(self):
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t)
            (tmp / "omie").mkdir()
            for d in DAYS:
                (tmp / "omie" / f"{d}.txt").write_text(synth(d), encoding="latin-1")
            now = dt.datetime(2026, 11, 21, 13, 15, tzinfo=dt.timezone.utc)
            with mock.patch.object(build, "DATA_DIR", tmp / "omie"), mock.patch.object(build, "PUBLIC_DIR", tmp / "public"), \
                    mock.patch.object(build, "ROOT", tmp), mock.patch("urllib.request.urlopen", side_effect=OSError("x")):
                build.build(DAYS[1], offline=True, now=now)
            build.STAMPS.clear()
            build.NAV_DAYS.clear()
            pub = tmp / "public"
            arch = hrefs(pub / "d" / "index.html")
            for d in DAYS:
                self.assertIn(f"/d/{d}/", arch)
            self.assertLess(arch.index("/d/2026-11-22/"), arch.index("/d/2026-11-20/"))  # newest first
            page = lambda d: pub / "d" / str(d) / "index.html"
            existing = {f"/d/{p.name}/" for p in (pub / "d").iterdir() if p.is_dir()}
            first, mid = hrefs(page(DAYS[0])), hrefs(page(DAYS[1]))
            self.assertIn("/d/2026-11-21/", first)
            self.assertIn("/d/2026-11-19/" if "/d/2026-11-19/" in existing else "/d/2026-11-21/", first)
            self.assertIn("/d/2026-11-20/", mid)
            self.assertIn("/d/2026-11-22/", mid)
            for d in DAYS:
                text = page(d).read_text(encoding="utf-8")
                for h in hrefs(page(d)):
                    if h != "/d/" and h.endswith("/") and "index.json" not in h:
                        self.assertIn(h, existing, f"{d}: link to missing page {h}")
                self.assertIn('rel="prev"', text) if d > min(DAYS) else None
                foot = text[text.index("<footer"):]
                self.assertIn('href="/d/"', foot)
            newest = max(int(p.name.replace("-", "")) for p in (pub / "d").iterdir() if p.is_dir())
            last = page(dt.datetime.strptime(str(newest), "%Y%m%d").date()).read_text(encoding="utf-8")
            self.assertNotIn('rel="next"', last)


if __name__ == "__main__":
    unittest.main()
