def ordered(items):
    items = items.sort()  # <<BUG>>
    return items
