"""T-0247: /como-ler/ is generated (temp dir, offline) and linked from the footer, sitemap and llms.txt."""
import datetime as dt
import json
import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import build  # noqa: E402


class ComoLer(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmpd = tempfile.TemporaryDirectory()
        tmp = Path(cls.tmpd.name)
        shutil.copytree(ROOT / "data" / "omie", tmp / "omie")
        day = max(dt.date.fromisoformat(p.stem) for p in (tmp / "omie").glob("2*.txt"))
        now = dt.datetime.combine(day - dt.timedelta(days=1), dt.time(13, 15), tzinfo=dt.timezone.utc)
        with mock.patch.object(build, "DATA_DIR", tmp / "omie"), mock.patch.object(build, "PUBLIC_DIR", tmp / "public"), \
                mock.patch.object(build, "ROOT", tmp), mock.patch("urllib.request.urlopen", side_effect=OSError("offline")):
            build.build(day, offline=True, now=now)
        cls.pub = tmp / "public"
        build.STAMPS.clear()

    @classmethod
    def tearDownClass(cls):
        cls.tmpd.cleanup()

    def test_page(self):
        h = (self.pub / "como-ler" / "index.html").read_text(encoding="utf-8")
        self.assertEqual(len(re.findall(r"<h1[ >]", h)), 1)
        self.assertIn("marginalpdbcpt", h)
        self.assertIn('lang="en"', h)
        self.assertNotIn("<script>", h)
        self.assertEqual(h.count("<script"), 1)
        m = re.search(r'<script type="application/ld\+json">(.*?)</script>', h, re.S)
        ld = json.loads(m.group(1))
        self.assertEqual(ld["url"], "https://luz.bymachines.ai/como-ler/")
        for hl in ("pt", "en", "x-default"):
            self.assertIn(f'<link rel="alternate" hreflang="{hl}" href="https://luz.bymachines.ai/como-ler/">', h)
        self.assertIn("IA", h)

    def test_hreflang_home_and_dated(self):
        """T-0251: / , /d/ and dated pages carry pt/en/x-default pointing at their own canonical."""
        day = sorted(p.name for p in (self.pub / "d").iterdir() if p.is_dir())[-1]
        for rel, url in (("index.html", "https://luz.bymachines.ai/"),
                         ("d/index.html", "https://luz.bymachines.ai/d/"),
                         (f"d/{day}/index.html", f"https://luz.bymachines.ai/d/{day}/")):
            h = (self.pub / rel).read_text(encoding="utf-8")
            for hl in ("pt", "en", "x-default"):
                self.assertIn(f'<link rel="alternate" hreflang="{hl}" href="{url}">', h, rel)
            self.assertIn(f'<link rel="canonical" href="{url}">', h)
            self.assertEqual(len(re.findall(r"<h1[ >]", h)), 1)

    def test_links(self):
        for page in ("index.html", "legal/index.html"):
            self.assertIn('href="/como-ler/"', (self.pub / page).read_text(encoding="utf-8"))
        self.assertIn("https://luz.bymachines.ai/como-ler/", (self.pub / "sitemap.xml").read_text())
        self.assertIn("https://luz.bymachines.ai/como-ler/", (self.pub / "llms.txt").read_text())


if __name__ == "__main__":
    unittest.main()
