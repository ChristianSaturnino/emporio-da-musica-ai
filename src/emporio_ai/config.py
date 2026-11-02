"""Application settings, read from the environment and an optional .env file."""

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MODEL = "gpt-6-luna"
# Measured against "low": about 20% faster and the same score on the clean set.
DEFAULT_REASONING_EFFORT = "none"


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    db_path: Path
    openai_model: str
    openai_reasoning_effort: str = DEFAULT_REASONING_EFFORT
    # Excluded from repr so the key cannot leak through logs or tracebacks.
    openai_api_key: str | None = field(default=None, repr=False)


def get_settings() -> Settings:
    load_dotenv(PROJECT_ROOT / ".env")
    return Settings(
        data_dir=PROJECT_ROOT / "data",
        db_path=PROJECT_ROOT / "emporio.db",
        openai_model=os.getenv("OPENAI_MODEL") or DEFAULT_MODEL,
        openai_reasoning_effort=os.getenv("OPENAI_REASONING_EFFORT") or DEFAULT_REASONING_EFFORT,
        openai_api_key=os.getenv("OPENAI_API_KEY") or None,
    )
