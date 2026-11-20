"""In-memory LRU cache for product lookups."""
from collections import OrderedDict

DEFAULT_TTL_SECONDS = 300
MAX_ENTRIES = 10_000


class Cache:
    def __init__(self, ttl_seconds: int = DEFAULT_TTL_SECONDS, max_entries: int = MAX_ENTRIES) -> None:
        self.ttl_seconds = ttl_seconds
        self.max_entries = max_entries
        self._items: OrderedDict[str, tuple[float, object]] = OrderedDict()

    def put(self, key: str, value: object, now: float) -> None:
        self._items[key] = (now + self.ttl_seconds, value)
        self._items.move_to_end(key)
        if len(self._items) > self.max_entries:
            self._items.popitem(last=False)

    def get(self, key: str, now: float) -> object | None:
        item = self._items.get(key)
        if item is None or item[0] < now:
            return None
        self._items.move_to_end(key)
        return item[1]
