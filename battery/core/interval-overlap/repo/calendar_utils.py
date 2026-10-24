def overlaps(a: tuple[int, int], b: tuple[int, int]) -> bool:
    """Whether two half-open intervals [start, end) share any time."""
    return a[0] <= b[1] and b[0] <= a[1]


def merge(intervals: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Merge overlapping half-open intervals into a sorted list of disjoint ones.

    Touching intervals (one ends where the next starts) are merged too.
    """
    merged: list[tuple[int, int]] = []
    for start, end in intervals:
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], end)
        else:
            merged.append((start, end))
    return merged
