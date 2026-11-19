def clean(name):
    name.strip()  # <<BUG>>
    name.lower()  # <<BUG>>
    return name
