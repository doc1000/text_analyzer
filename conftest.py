"""
Root conftest.py — adds src/ to sys.path so graph_engine is importable
without a package install step.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))
