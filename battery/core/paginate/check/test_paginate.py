import pytest
from pager import paginate

ITEMS = list(range(1, 11))


def test_first_page():
    assert paginate(ITEMS, 1, 3) == [1, 2, 3]


def test_middle_page():
    assert paginate(ITEMS, 2, 3) == [4, 5, 6]


def test_last_partial_page():
    assert paginate(ITEMS, 4, 3) == [10]


def test_page_past_the_end_is_empty():
    assert paginate(ITEMS, 5, 3) == []


def test_page_zero_raises():
    with pytest.raises(ValueError):
        paginate(ITEMS, 0, 3)


def test_per_page_zero_raises():
    with pytest.raises(ValueError):
        paginate(ITEMS, 1, 0)
