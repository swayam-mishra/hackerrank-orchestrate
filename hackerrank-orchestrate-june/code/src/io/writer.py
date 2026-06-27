"""Output serialization. Writes the 14 columns in EXACT order, UTF-8, '\\n' line
endings, QUOTE_ALL so embedded ';' and '|' round-trip. Asserts the header equals
the contract and (optionally) the row count."""
from __future__ import annotations

import csv
from pathlib import Path

from src.schema import OUTPUT_COLUMNS, OutputRow


def write_output(rows: list[OutputRow], path: Path, expected_count: int | None = None) -> None:
    if expected_count is not None and len(rows) != expected_count:
        raise ValueError(f"row count {len(rows)} != expected {expected_count}")
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f, fieldnames=list(OUTPUT_COLUMNS), quoting=csv.QUOTE_ALL, lineterminator="\n"
        )
        writer.writeheader()
        for r in rows:
            d = r.to_csv_dict()
            # paranoia: enforce exact column set/order at write time
            if tuple(d.keys()) != OUTPUT_COLUMNS:
                raise ValueError("OutputRow.to_csv_dict produced wrong columns")
            writer.writerow(d)
