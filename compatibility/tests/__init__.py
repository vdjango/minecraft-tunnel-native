"""Standalone node tests; run with unittest discovery from the repository root."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
