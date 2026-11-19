"""Internal link check (T-0205): every href/src starting with '/' or https://luz.bymachines.ai/ in every generated
*.html, and every <loc> in sitemap.xml, must resolve to a generated file. Builds offline from the cached data/omie/
into a temp dir (never public/ or data/).
Run: python3 -m unittest tests.test_links"""
import datetime as dt
import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from urllib.parse import urlsplit, unquote

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import build  # noqa: E402

TMP = None
OUT = None
HOST = "https://luz.bymachines.ai"
STATIC_SUFFIXES = {".css", ".svg", ".png", ".ico"}


def setUpModule():
    global TMP, OUT
    TMP = tempfile.TemporaryDirectory()
    tmp = Path(TMP.name)
    shutil.copytree(ROOT / "data" / "omie", tmp / "omie")
    days = sorted(p.stem for p in (tmp / "omie").glob("????-??-??.txt"))
    target = dt.date.fromisoformat(days[-1])
    with mock.patch.object(build, "DATA_DIR", tmp / "omie"), mock.patch.object(build, "PUBLIC_DIR", tmp / "public"), \
            mock.patch.object(build, "ROOT", tmp), mock.patch("urllib.request.urlopen", side_effect=OSError("network disabled")):
        build.build(target, offline=True)
    OUT = tmp / "public"


def tearDownModule():
    TMP.cleanup()


def resolve(url):
    """Return the generated file a site URL maps to, or None."""
    if url.startswith(HOST):
        url = url[len(HOST):] or "/"
    path = unquote(urlsplit(url).path)
    if not path.startswith("/"):
        return None
    f = OUT / path.lstrip("/")
    if path.endswith("/"):
        f = f / "index.html"
    if f.is_file():
        return f
    # hand-written static assets (style.css, brand/*.svg|png) are committed, not generated: read-only fallback
    static = ROOT / "public" / path.lstrip("/")
    if static.suffix in STATIC_SUFFIXES and static.is_file():
        return static
    return None


def internal(url):
    return (url.startswith("/") and not url.startswith("//")) or url.startswith(HOST + "/") or url == HOST


class Links(unittest.TestCase):
    def test_html_links(self):
        pages = sorted(OUT.rglob("*.html"))
        self.assertGreater(len(pages), 5)
        checked, broken = 0, []
        for p in pages:
            h = p.read_text(encoding="utf-8")
            for m in re.finditer(r'\b(?:href|src)="([^"]*)"', h):
                url = m.group(1).replace("&amp;", "&")
                if not internal(url):
                    continue
                checked += 1
                if resolve(url) is None:
                    broken.append(f"{p.relative_to(OUT)} -> {url}")
        self.assertGreater(checked, 20)
        self.assertEqual(sorted({b.split(" -> ")[1] for b in broken}), [], "broken internal links")

    def test_sitemap_locs(self):
        xml = (OUT / "sitemap.xml").read_text(encoding="utf-8")
        locs = re.findall(r"<loc>([^<]+)</loc>", xml)
        self.assertGreater(len(locs), 5)
        for want in (HOST + "/", HOST + "/d/"):
            self.assertIn(want, locs)
        broken = [u for u in locs if not u.startswith(HOST + "/") or resolve(u) is None]
        self.assertEqual(broken, [], "sitemap <loc> not generated")


if __name__ == "__main__":
    unittest.main()
