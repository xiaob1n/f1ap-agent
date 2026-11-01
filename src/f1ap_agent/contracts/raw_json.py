"""Bounded JSON grammar reader; preserve pairs and number lexemes for later checks."""
import json
import re

NUMBER = re.compile(r'-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?')


class StructuralError(ValueError):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


def decode(text, object_type, number_type, max_depth):
    """First syntax/depth failure in token order, matching the Java stream parser."""
    position = 0
    length = len(text)

    def fail(code='JSON'):
        raise StructuralError(code)

    def whitespace():
        nonlocal position
        while position < length and text[position] in ' \t\r\n':
            position += 1

    def string():
        nonlocal position
        if position >= length or text[position] != '"':
            fail()
        try:
            result, position = json.decoder.scanstring(text, position + 1, True)
        except (ValueError, UnicodeError):
            fail()
        return result

    def value(depth):
        nonlocal position
        whitespace()
        if position == length:
            fail()
        token = text[position]
        if token == '"':
            return string()
        if token in '{[':
            if depth >= max_depth:
                fail('DEPTH')
            position += 1
            is_object = token == '{'
            closing = '}' if is_object else ']'
            result = object_type() if is_object else []
            whitespace()
            if position < length and text[position] == closing:
                position += 1
                return result
            while True:
                whitespace()
                if is_object:
                    key = string()
                    whitespace()
                    if position >= length or text[position] != ':':
                        fail()
                    position += 1
                    result.append((key, value(depth + 1)))
                else:
                    result.append(value(depth + 1))
                whitespace()
                if position >= length:
                    fail()
                delimiter = text[position]
                position += 1
                if delimiter == closing:
                    return result
                if delimiter != ',':
                    fail()
                # Next iteration rejects missing values and trailing commas.
        for literal, result in (('true', True), ('false', False), ('null', None)):
            if text.startswith(literal, position):
                position += len(literal)
                return result
        number = NUMBER.match(text, position)
        if number:
            position = number.end()
            return number_type(number.group())
        fail()

    result = value(0)
    whitespace()
    if position != length:
        fail()
    return result
