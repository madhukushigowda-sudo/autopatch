"""Decorator-based registry with closures bound by default argument."""
_HANDLERS = {}


def register(name):
    def decorator(func):
        _HANDLERS[name] = func
        return func
    return decorator


@register("double")
def double(x):
    return x * 2


@register("negate")
def negate(x):
    return -x


def make_adders(numbers):
    adders = []
    for n in numbers:
        adders.append(lambda x, n=n: x + n)
    return adders


def dispatch(name, value):
    handler = _HANDLERS.get(name)
    if handler is None:
        raise KeyError(f"unknown handler {name!r}")
    return handler(value)
