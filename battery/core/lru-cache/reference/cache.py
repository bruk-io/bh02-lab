from collections import OrderedDict


class LRUCache:
    """A cache holding at most `capacity` entries; adding one more evicts the least recently used.

    Both `get` (when it finds the key) and `put` count as a use. Raises ValueError when
    `capacity` is below 1.
    """

    def __init__(self, capacity: int) -> None:
        if capacity < 1:
            raise ValueError("capacity must be at least 1")
        self._capacity = capacity
        self._data: OrderedDict = OrderedDict()

    def get(self, key, default=None):
        """The value for `key`, or `default` when it is not cached."""
        if key not in self._data:
            return default
        self._data.move_to_end(key)
        return self._data[key]

    def put(self, key, value) -> None:
        """Cache `value` under `key`, replacing any value it had."""
        self._data[key] = value
        self._data.move_to_end(key)
        if len(self._data) > self._capacity:
            self._data.popitem(last=False)

    def __len__(self) -> int:
        return len(self._data)
