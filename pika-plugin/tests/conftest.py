from __future__ import annotations

import sys
from pathlib import Path


SERVER_DIR = Path(__file__).resolve().parents[1] / "server"
sys.path.insert(0, str(SERVER_DIR))
