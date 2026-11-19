def read(path):
    try:
        with open(path) as fh:
            return fh.read()
    except:  # <<BUG>>
        return None
