import json

from .errors import FormatError


def strip_jsonc(text):
    out = []
    i = 0
    n = len(text)
    in_string = False
    pending_comma_idx = None
    seen_significant_since_comma = False
    line_no = 1
    while i < n:
        c = text[i]
        if c == "\n":
            line_no += 1
        if in_string:
            out.append(c)
            if c == "\\" and i + 1 < n:
                nxt = text[i + 1]
                out.append(nxt)
                if nxt == "\n":
                    raise FormatError(f"jsonc: raw newline inside string near line {line_no}")
                i += 2
                continue
            if c == '"':
                in_string = False
            elif c == "\n":
                raise FormatError(f"jsonc: unterminated string near line {line_no}")
            i += 1
            continue
        if c == '"':
            out.append(c)
            in_string = True
            seen_significant_since_comma = True
            i += 1
            continue
        if c == "/" and i + 1 < n and text[i + 1] == "/":
            while i < n and text[i] != "\n":
                i += 1
            continue
        if c == "/" and i + 1 < n and text[i + 1] == "*":
            i += 2
            closed = False
            while i < n:
                if text[i] == "*" and i + 1 < n and text[i + 1] == "/":
                    i += 2
                    closed = True
                    break
                if text[i] == "\n":
                    line_no += 1
                i += 1
            if not closed:
                raise FormatError("jsonc: unterminated block comment")
            out.append(" ")
            continue
        if c == ",":
            out.append(c)
            pending_comma_idx = len(out) - 1
            seen_significant_since_comma = False
            i += 1
            continue
        if c in ("}", "]"):
            if pending_comma_idx is not None and not seen_significant_since_comma:
                out[pending_comma_idx] = ""
                pending_comma_idx = None
            out.append(c)
            seen_significant_since_comma = True
            i += 1
            continue
        if not c.isspace():
            seen_significant_since_comma = True
        out.append(c)
        i += 1
    if in_string:
        raise FormatError("jsonc: unterminated string at end of input")
    return "".join(out)


def _no_dup_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise FormatError(f"json/jsonc: duplicate key: {key!r}")
        result[key] = value
    return result


def loads_strict(text):
    from .safety import ensure_encodable

    try:
        parsed = json.loads(text, object_pairs_hook=_no_dup_pairs)
    except RecursionError:
        raise FormatError("json/jsonc: nesting too deep")
    except json.JSONDecodeError as exc:
        raise FormatError(f"invalid JSON near line {exc.lineno}, column {exc.colno}: {exc.msg}")
    ensure_encodable(parsed)
    return parsed


def loads_jsonc(text):
    return loads_strict(strip_jsonc(text))


def dumps_pretty(obj):
    return json.dumps(obj, indent=2, ensure_ascii=False) + "\n"
