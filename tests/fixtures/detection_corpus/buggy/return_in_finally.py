def run(task):
    try:
        return task()
    finally:
        return None  # <<BUG>>
