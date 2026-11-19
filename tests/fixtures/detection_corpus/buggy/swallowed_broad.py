def sync(items, send):
    for item in items:
        try:
            send(item)
        except Exception:  # <<BUG>>
            pass
