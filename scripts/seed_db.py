"""Create or rebuild the local SQLite database from the challenge CSV files."""

import sys

from emporio_ai.config import get_settings
from emporio_ai.data.loader import build_database
from emporio_ai.domain.errors import DatasetError


def main() -> int:
    settings = get_settings()
    try:
        counts = build_database(settings.data_dir, settings.db_path)
    except DatasetError as exc:
        print(f"Import failed: {exc}")
        return 1
    for table, count in counts.items():
        print(f"{table:<12} {count:>3} rows")
    print(f"Database ready: {settings.db_path.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
