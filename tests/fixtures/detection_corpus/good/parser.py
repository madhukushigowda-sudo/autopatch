"""A small command parser."""


class ParseError(Exception):
    pass


def parse(line):
    parts = line.split()
    if not parts:
        raise ParseError("empty command")
    command, *args = parts
    return command, args


def parse_int(token):
    try:
        return int(token)
    except ValueError:
        return None


def parse_many(lines):
    results = []
    for line in lines:
        try:
            results.append(parse(line))
        except ParseError:
            continue
    return results
