def total_by_category(text: str) -> dict[str, float]:
    """Sum `amount` per `category` for a CSV with the header date,description,category,amount."""
    totals: dict[str, float] = {}
    lines = text.strip().split("\n")
    for line in lines[1:]:
        date, description, category, amount = line.split(",")
        totals[category] = totals.get(category, 0.0) + float(amount)
    return totals
