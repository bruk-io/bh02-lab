def paginate(items, page, per_page):
    """Return the items on `page` (1-based), `per_page` at a time.

    A page past the end is empty. Raises ValueError when `page` or `per_page` is below 1.
    """
    if page < 1 or per_page < 1:
        raise ValueError("page and per_page must be at least 1")
    start = (page - 1) * per_page
    return items[start:start + per_page]
