"""Keys read by the /ha/ YAML exist in available JSON; unguarded reads exist in unavailable JSON (T-0091)."""
import datetime as dt
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import build  # noqa: E402


class HaKeys(unittest.TestCase):
    def test_keys(self):
        built = dt.datetime(2026, 11, 3, 13, 15, tzinfo=dt.timezone.utc)
        day = dt.date(2026, 11, 3)
        fx = build.parse_omie((ROOT / "data" / "fixtures" / "marginalpdbcpt_20261103.1").read_text(encoding="latin-1"))
        files = {day: fx}
        hours = build.lisbon_hours(day, files)
        st = build.stats(hours)
        avail = build.day_json(day, hours, st, build.comparison(day, files), build.load_tariff(), built, build.omie_filename(day))
        self.assertTrue(avail["available"])
        unavail = build.unavailable_json(dt.date(2026, 11, 4), built, 404, None, with_note=True)
        today_unavail = build.unavailable_json(dt.date(2026, 11, 3), built)
        for j in (avail, unavail, today_unavail):
            self.assertRegex(j["built_utc"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\+00:00$")
            self.assertEqual(j["timezone"], "Europe/Lisbon")
            self.assertIn("AI agent", j["disclosure"])
            self.assertIn("Dmitriy", j["disclosure"])
        self.assertEqual(set(today_unavail), {"date", "available", "built_utc", "timezone", "disclosure"})
        self.assertFalse(unavail["available"])
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
