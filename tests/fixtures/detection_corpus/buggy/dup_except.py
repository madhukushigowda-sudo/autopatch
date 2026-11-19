def parse(text):
    try:
        return int(text)
    except ValueError:
        return 0
    except ValueError:  # <<BUG>>
        return -1
