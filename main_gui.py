"""Entrada de desarrollo: ``python main_gui.py``."""
from pathlib import Path
import sys

SRC = Path(__file__).resolve().parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from gui.main import main, run  # noqa: E402


if __name__ == "__main__":
    run()
