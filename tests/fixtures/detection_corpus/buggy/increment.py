def bump(counter):
    ++counter  # <<BUG>>
    return counter
