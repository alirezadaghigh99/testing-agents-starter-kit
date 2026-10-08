"""Harness for black-box and white-box unit test generation agents."""

import os
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent

load_dotenv(REPO_ROOT / ".env")
os.environ.setdefault("MSWEA_SILENT_STARTUP", "1")
