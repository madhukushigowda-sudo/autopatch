def make_callbacks(names):
    callbacks = []
    for name in names:
        callbacks.append(lambda: print(name))  # <<BUG>>
    return callbacks
