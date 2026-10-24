import pytest
from cache import LRUCache


def test_get_missing_gives_default():
    assert LRUCache(2).get("x", 7) == 7


def test_put_then_get():
    cache = LRUCache(2)
    cache.put("a", 1)
    assert cache.get("a") == 1 and len(cache) == 1


def test_evicts_least_recently_used():
    cache = LRUCache(2)
    cache.put("a", 1)
    cache.put("b", 2)
    cache.get("a")
    cache.put("c", 3)
    assert cache.get("b") is None and cache.get("a") == 1 and cache.get("c") == 3


def test_put_counts_as_use():
    cache = LRUCache(2)
    cache.put("a", 1)
    cache.put("b", 2)
    cache.put("a", 10)
    cache.put("c", 3)
    assert cache.get("a") == 10 and cache.get("b") is None


def test_never_over_capacity():
    cache = LRUCache(3)
    for i in range(10):
        cache.put(i, i)
    assert len(cache) == 3


def test_capacity_below_one_raises():
    with pytest.raises(ValueError):
        LRUCache(0)
