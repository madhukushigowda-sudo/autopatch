def broken(x):
    if x > 1  # <<BUG>>
        return x
    return 0
