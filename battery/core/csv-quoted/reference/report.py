import csv
import io


def total_by_category(text: str) -> dict[str, float]:
    """Sum `amount` per `category` for a CSV with the header date,description,category,amount."""
    totals: dict[str, float] = {}
    for row in csv.DictReader(io.StringIO(text)):
        if not row or row.get("category") is None:
            continue
        totals[row["category"]] = totals.get(row["category"], 0.0) + float(row["amount"])
    return totals
