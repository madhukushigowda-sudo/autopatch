"""A tiny cache with an explicit sentinel."""
_MISSING = object()


class Cache:
    def __init__(self, loader):
        self._loader = loader
        self._data = {}

    def get(self, key):
        value = self._data.get(key, _MISSING)
        if value is _MISSING:
            value = self._data[key] = self._loader(key)
        return value

    def drop(self, key):
        try:
            del self._data[key]
        except KeyError:
            pass

    def __len__(self):
        return len(self._data)
