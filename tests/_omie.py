"""Hermetic OMIE cache for tests (T-0257): a frozen snapshot (days up to 2026-11-22) in tests/data/omie/.
Tests never read the real data/omie/, so a new daily file there cannot change their result."""
import shutil
from pathlib import Path

SNAPSHOT = Path(__file__).resolve().parent / "data" / "omie"


def copy_omie(dst: Path) -> None:
    shutil.copytree(SNAPSHOT, dst)
