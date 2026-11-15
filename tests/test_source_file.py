"""The OMIE file name shown/published is the one actually cached (T-0156). Run: python3 -m unittest tests.test_source_file"""
import datetime as dt
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import build  # noqa: E402

DAY = dt.date(2026, 11, 11)


class SourceFile(unittest.TestCase):
    def name_for(self, header: str) -> str:
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / f"{DAY.isoformat()}.txt").write_text(f"{header};\n2026;11;11;1;1;1;\n*\n", encoding="latin-1")
            with mock.patch.object(build, "DATA_DIR", Path(d)):
                return build.omie_filename(DAY)

    def test_marginalpdbc(self):
        self.assertEqual(self.name_for("MARGINALPDBC"), "marginalpdbc_20261111.1")

    def test_marginalpdbcpt(self):
        self.assertEqual(self.name_for("MARGINALPDBCPT"), "marginalpdbcpt_20261111.1")


if __name__ == "__main__":
    unittest.main()
