"""Keys read by the /ha/ YAML exist in available JSON; unguarded reads exist in unavailable JSON (T-0091)."""
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import build  # noqa: E402


class HaKeys(unittest.TestCase):
    def test_keys(self):
        import json
        avail = json.loads((ROOT / "public" / "d" / "2026-11-03" / "index.json").read_text()) \
            if (ROOT / "public" / "d" / "2026-11-03" / "index.json").exists() else None
        if avail is None:
            ds = sorted((ROOT / "public" / "d").glob("*/index.json"))
            avail = json.loads(ds[-1].read_text())
        self.assertTrue(avail["available"])
        unavail = {"date": "2026-11-09", "available": False, "note": "x"}
        for name in ("YAML1", "YAML2"):
            y = getattr(build, name)
            for m in re.finditer(r"\{\{(.*?)\}\}", y, re.S):
                tpl = m.group(1)
                paths = re.findall(r"value_json((?:\.\w+)+)", tpl)
                guarded = "value_json.available" in tpl
                for p in paths:
                    cur = avail
                    for k in p.strip(".").split("."):
                        self.assertIn(k, cur, f"{name}: {p} missing in available JSON")
                        cur = cur[k]
                    top = p.strip(".").split(".")[0]
                    if top in ("cheap_window", "dear_window", "stats"):
                        self.assertTrue(guarded, f"{name}: {p} read without available guard")
                    else:
                        self.assertIn(top, unavail, f"{name}: unguarded {p} absent when unavailable")
            attrs = re.findall(r"^\s+- (\w+)$", y.split("json_attributes:")[1].split("- name:")[0], re.M)
            for a in attrs:
                self.assertIn(a, avail)


if __name__ == "__main__":
    unittest.main()
