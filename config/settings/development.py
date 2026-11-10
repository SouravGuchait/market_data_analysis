"""Local development settings loaded from the environment or project ``.env``."""

from __future__ import annotations

from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent.parent
load_dotenv(BASE_DIR / ".env")

from .base import *  # noqa: E402,F403

DEBUG = True
