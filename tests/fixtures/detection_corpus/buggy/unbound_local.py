def label(flag):
    if flag:
        text = "on"
    print(count)  # <<BUG>>
    count = 1
    return text
