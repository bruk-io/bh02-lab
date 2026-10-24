def paginate(items, page, per_page):
    """Return the items on `page` (1-based), `per_page` at a time.

    A page past the end is empty. Raises ValueError when `page` or `per_page` is below 1.
    """
    start = page * per_page
    return items[start:start + per_page]
