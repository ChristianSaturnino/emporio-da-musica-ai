"""Print an inventory of the challenge datasets and fail if any of them looks wrong."""

import csv
import sys
from pathlib import Path

from pypdf import PdfReader

from emporio_ai.config import get_settings

EXPECTED_ROWS = {
    "categories.csv": 9,
    "customers.csv": 50,
    "order_items.csv": 22,
    "orders.csv": 20,
    "products.csv": 65,
    "promotions.csv": 25,
}
POLICY_MANUAL = "politicas_da_loja.pdf"
EXPECTED_POLICY_PAGES = 8


def inspect_csv(path: Path, expected_rows: int) -> bool:
    if not path.exists():
        print(f"{path.name:<24} MISSING")
        return False
    with path.open(encoding="utf-8", newline="") as file:
        reader = csv.DictReader(file)
        rows = sum(1 for _ in reader)
        columns = ", ".join(reader.fieldnames or [])
    ok = rows == expected_rows
    print(f"{path.name:<24} {rows:>3} rows (expected {expected_rows})  [{columns}]")
    return ok


def inspect_policy_manual(path: Path) -> bool:
    if not path.exists():
        print(f"{path.name:<24} MISSING")
        return False
    pages = len(PdfReader(path).pages)
    print(f"{path.name:<24} {pages:>3} pages (expected {EXPECTED_POLICY_PAGES})")
    return pages == EXPECTED_POLICY_PAGES


def main() -> int:
    data_dir = get_settings().data_dir
    results = [inspect_csv(data_dir / name, rows) for name, rows in EXPECTED_ROWS.items()]
    results.append(inspect_policy_manual(data_dir / POLICY_MANUAL))
    if not all(results):
        print("Dataset inventory FAILED")
        return 1
    print("Dataset inventory OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
