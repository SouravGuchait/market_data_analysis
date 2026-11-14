"""Local development settings loaded from the environment or project ``.env``."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent.parent
ENV_FILE = Path(os.environ.get("MDA_ENV_FILE", BASE_DIR)).expanduser()
load_dotenv(ENV_FILE)

from .base import *  # noqa: E402,F403

DEBUG = True
