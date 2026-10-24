class LRUCache:
    """A cache holding at most `capacity` entries; adding one more evicts the least recently used.

    Both `get` (when it finds the key) and `put` count as a use. Raises ValueError when
    `capacity` is below 1.
    """

    def __init__(self, capacity: int) -> None:
        raise NotImplementedError

    def get(self, key, default=None):
        """The value for `key`, or `default` when it is not cached."""
        raise NotImplementedError

    def put(self, key, value) -> None:
        """Cache `value` under `key`, replacing any value it had."""
        raise NotImplementedError

    def __len__(self) -> int:
        raise NotImplementedError
